import asyncio
import json
import logging
import os
from aiohttp import ClientSession, ClientError, ClientResponseError
from typing import Dict, List, Optional
from urllib.parse import urlencode
from yarl import URL

from azure.identity.aio import DefaultAzureCredential
from azure.storage.queue.aio import QueueClient
from azure.storage.queue import QueueMessage


class ProjectRetrievalError(Exception):
    """Custom exception for errors during project ID retrieval."""

    pass


# Configuration - Required
SNYK_TOKEN = os.environ.get("SNYK_TOKEN")
STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME")
STORAGE_QUEUE_NAME = os.environ.get("STORAGE_QUEUE_NAME")

# Configuration - Optional
SNYK_REST_API_URL = os.environ.get("SNYK_REST_API_URL", "https://api.snyk.io/rest/")
SNYK_REST_API_VERSION = os.environ.get("SNYK_REST_API_VERSION", "2024-10-15")
SNYK_V1_API_URL = os.environ.get("SNYK_V1_API_URL", "https://api.snyk.io/v1/")
MAX_TIMEOUT_MINUTES = int(os.environ.get("MAX_TIMEOUT_MINUTES", 30))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", 5))
QUEUE_POLLING_INTERVAL_SECONDS = float(
    os.environ.get("QUEUE_POLLING_INTERVAL_SECONDS", 10)
)
VISIBILITY_TIMEOUT_SECONDS = int(os.environ.get("VISIBILITY_TIMEOUT_SECONDS", 30))

# Logging setup
logging.getLogger("azure").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def check_vars() -> bool:
    """
    Checks if required environment variables are present.

    Returns:
        True if all require environment variables present, False otherwise
    """
    required = ["SNYK_TOKEN", "STORAGE_ACCOUNT_NAME", "STORAGE_QUEUE_NAME"]
    missing = []

    for var in required:
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        logger = logging.getLogger(__name__)
        logger.error(
            f"The following required environment variables are not set: {', '.join(missing)}"
        )
        return False
    return True


async def get_queue_client() -> QueueClient:
    """Retrieves an Azure Storage Queue client with managed identity."""
    credential = DefaultAzureCredential()
    queue_url = f"https://{STORAGE_ACCOUNT_NAME}.queue.core.windows.net/"
    queue_name = STORAGE_QUEUE_NAME
    return QueueClient(queue_url, queue_name, credential=credential)


class SnykApiClient:
    def __init__(self, snyk_token: str):
        self.token = snyk_token
        self.rest_api_url = SNYK_REST_API_URL
        self.rest_api_version = SNYK_REST_API_VERSION
        self.v1_api_url = SNYK_V1_API_URL
        self._session = None

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                headers={"Authorization": f"token {self.token}"}
            )
        return self._session

    async def close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, url: str, params: Optional[Dict[str, str]] = None):
        session = await self._get_session()
        try:
            full_url = url
            if params:
                full_url = f"{url}?{params}"
            async with session.get(URL(full_url, encoded=True)) as response:
                response.raise_for_status()
                return await response.json()
        except ClientResponseError as e:
            logger.error(f"HTTP GET error for {url} (status {e.status}): {e}")
            raise e  # Re-raise the specific HTTP error
        except ClientError as e:
            logger.error(f"Client error during GET request to {url}: {e}")
            raise e  # Re-raise other client-related errors
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during the GET request to {url}: {e}"
            )
            raise

    async def _post(self, url: str, json_data: Optional[Dict] = None):
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}
        try:
            async with session.post(url, headers=headers, json=json_data) as response:
                if response.status == 422 and "tags" in url:
                    logger.debug(
                        f"Received 422 for tagging URL {url}. Project already tagged."
                    )
                    return response
                response.raise_for_status()
                return response
        except ClientError as e:
            logger.error(f"HTTP POST error for {url}: {e}")
            return None

    async def get_import_job_status(self, import_job_url: str) -> Optional[str]:
        """
        Retrieves the import status of a Snyk import job.

        Args:
            import_job_url: The URL of the Snyk import job.

        Returns:
            The status of the import job, or None if an error occurred.
        """
        data = await self._get(import_job_url)
        if data:
            return data.get("status")
        return None

    async def retrieve_project_ids(
        self, target_name: str, branch: str, org_id: str
    ) -> Optional[List[str]]:
        """
        Retrieves project IDs from Snyk API for a given target name, handling pagination.

        Args:
            target_name: The name (prefix) of the target.
            branch: The branch name of the target.
            org_id: The Snyk organization ID.

        Returns:
            A list of all matching project IDs, or None if an error occurred.

        Raises:
            ProjectRetrievalError: If an error occurs during the API request.
        """
        all_project_ids = []
        url = f"{self.rest_api_url}orgs/{org_id}/projects"
        params = {
            "version": self.rest_api_version,
            "names_start_with": target_name,
            "target_reference": branch,
            "origins": "azure-repos",
            "limit": 100,
        }

        while url:
            try:
                response_json = await self._get(url, params=urlencode(params, safe=""))
                if response_json:
                    data = response_json.get("data")
                    project_ids = [
                        item["id"] for item in data if item.get("type") == "project"
                    ]
                    all_project_ids.extend(project_ids)
                    next_page = response_json.get("links").get("next")
                    if next_page:
                        url = f"{"https://api.snyk.io"}{next_page}"
                        params = {}
                    else:
                        url = None
                else:
                    raise ProjectRetrievalError(
                        f"Empty response received while retrieving project IDs for {target_name}/{branch}"
                    )
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred while retrieving project IDs for {target_name}/{branch}: {e}"
                )
                raise Exception(e)

        return all_project_ids

    async def _tag_project(
        self, project_id: str, tag: Dict[str, str], org_id: str
    ) -> bool:
        """
        Tags a single project in Snyk with the given tag.

        Args:
            project_id: A Snyk project ID to tag.
            tags: A dictionary which represents a tag with 'key' and 'value'.
            org_id: The Snyk organization ID.

        Returns:
            True if project was tagged successfully (including already tagged), False if any tagging failed due to other errors.
        """
        url = f"{self.v1_api_url}org/{org_id}/project/{project_id}/tags"
        response = await self._post(url, json_data=tag)
        if response:
            if response.status == 200:
                logger.info(f"Successfully tagged project {project_id} with {tag}")
                return True
            elif response.status == 422:
                logger.info(f"Project {project_id} already tagged with {tag}.")
                return True
            else:
                logger.error(
                    f"Failed to tag project {project_id} with {tag}. Status: {response.status}"
                )
                return False
        else:
            logger.error(
                f"Failed to tag project {project_id} with {tag} due to network error."
            )
            return False

    async def tag_projects(
        self, project_ids: List[str], tags: List[Dict[str, str]], org_id: str
    ) -> bool:
        """
        Tags projects in Snyk with the given tags.

        Args:
            project_ids: A list of Snyk project IDs to tag.
            tags: A list of dictionaries, where each dictionary represents a tag with 'key' and 'value'.
            org_id: The Snyk organization ID.

        Returns:
            True if all tagging attempts for all projects were successful (including already tagged), False if any tagging failed due to other errors.
        """
        all_successful = True
        for project_id in project_ids:
            for tag in tags:
                if not await self._tag_project(project_id, tag, org_id):
                    logger.error(f"Failed to tag project {project_id} with {tag}.")
                    all_successful = False
        return all_successful


async def renew_lease(queue_client: QueueClient, message: QueueMessage):
    """
    Periodically renews the visibility timeout of a message.

    Args:
        message: The message object received from the Azure Storage Queue.
        queue_client: The Azure Storage Queue client instance.
    """
    while True:
        await asyncio.sleep(VISIBILITY_TIMEOUT_SECONDS // 2)
        try:
            await queue_client.update_message(
                message,
                visibility_timeout=VISIBILITY_TIMEOUT_SECONDS,  # Reset to the initial visibility timeout
            )
            logger.info(f"Renewed lease for message: {message.id}")
        except Exception as e:
            logger.error(f"Error renewing lease for message {message.id}: {e}")
            break


async def process_message(
    message: QueueMessage, queue_client: QueueClient, api_client: SnykApiClient
) -> None:
    """
    Processes a message from the queue.

    Args:
        message: The message object received from the Azure Storage Queue.
        queue_client: The Azure Storage Queue client instance.
        api_client: An instance of the SnykApiClient.

    Returns:
        None
    """
    renewal_task = None
    try:
        renewal_task = asyncio.create_task(renew_lease(queue_client, message))

        content = json.loads(message.content)
        target_name = content["target_name"]
        branch = content["branch"]
        tags = content["tags"]
        org_id = content["org_id"]
        import_job_url = content["import_job_url"]
        attempts = content.get("attempts", 0)

        logger.info(
            f"Processing message: {message.id}, Target: {target_name}, Branch: {branch}, Attempts: {attempts}"
        )

        if attempts >= MAX_ATTEMPTS:
            logger.error(f"Message {message.id} exceeded maximum attempts, deleting.")
            await queue_client.delete_message(message)
            return

        import_status = await api_client.get_import_job_status(import_job_url)
        if import_status == "complete":
            logger.info(
                f"Import complete for target: {target_name}({branch}). Retrieving project IDs..."
            )
            try:
                project_ids = await api_client.retrieve_project_ids(
                    target_name, branch, org_id
                )
                logger.info(
                    f"Project IDs retrieved for target: {target_name}({branch})."
                )
                if project_ids:
                    if await api_client.tag_projects(project_ids, tags, org_id):
                        await queue_client.delete_message(message)
                        logger.info(f"Message {message.id} processed successfully.")
                    else:
                        logger.error(
                            f"Failed to tag one or more projects for {target_name}/{branch}, requeueing message: {message.id}"
                        )
                        await requeue_message(message, queue_client, attempts)
                else:
                    await queue_client.delete_message(message)
                    logger.info(
                        f"No project IDs found for target: {target_name}({branch}), deleted message: {message.id}."
                    )
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred: {e}, requeueing message {message.id}"
                )
                await requeue_message(message, queue_client, attempts)
        elif import_status == "pending":
            logger.info(
                f"Import status pending for target: {target_name}({branch}), requeueing message {message.id}"
            )
            await requeue_message(message, queue_client, attempts)
        else:
            logger.error(
                f"Import status failed for target: {target_name}({branch}), requeueing message {message.id}"
            )
            await requeue_message(message, queue_client, attempts)

    except (json.JSONDecodeError, KeyError) as e:
        await queue_client.delete_message(message)
        logger.error(f"Invalid message format: {e}, deleted message {message.id}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred: {e}, requeueing message {message.id}"
        )
        await requeue_message(message, queue_client, attempts)
    finally:
        if renewal_task:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                logger.debug(f"Lease renewal task cancelled for message: {message.id}")


async def requeue_message(
    message: QueueMessage, queue_client: QueueClient, attempts: int
) -> None:
    """
    Requeues a message with incremented attempts and increases timeout with each attempt.

    Args:
        message: The message object to requeue.
        queue_client: The Azure Storage Queue client instance.
        attempts: The current number of attempts for this message.

    Returns:
        None
    """
    attempts += 1
    content = json.loads(message.content)
    content["attempts"] = attempts

    timeout_minutes = MAX_TIMEOUT_MINUTES * (0.5 ** (MAX_ATTEMPTS - attempts))
    timeout_seconds = int(timeout_minutes * 60)

    await queue_client.update_message(
        message, visibility_timeout=timeout_seconds, content=json.dumps(content)
    )
    logger.info(
        f"Message {message.id} requeued, Attempts: {attempts}, Timeout: {timeout_minutes:.2f} minutes"
    )


async def main() -> None:
    """Main function to run the message processing loop."""
    if not check_vars():
        return

    snyk_api_client = SnykApiClient(SNYK_TOKEN)
    queue_client = await get_queue_client()
    try:
        while True:
            messages = queue_client.receive_messages(
                messages_per_page=32, visibility_timeout=VISIBILITY_TIMEOUT_SECONDS
            )
            tasks = []
            async for message in messages:
                task = asyncio.create_task(
                    process_message(message, queue_client, snyk_api_client)
                )
                tasks.append(task)

            if tasks:
                await asyncio.gather(*tasks)
            await asyncio.sleep(QUEUE_POLLING_INTERVAL_SECONDS)
    finally:
        await snyk_api_client.close_session()


if __name__ == "__main__":
    asyncio.run(main())
