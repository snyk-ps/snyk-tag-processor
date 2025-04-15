import asyncio
import json
import logging
import os
from typing import Dict, List, Optional
from urllib.parse import urlencode
from yarl import URL

import aiohttp
from azure.identity.aio import DefaultAzureCredential
from azure.storage.queue.aio import QueueClient

# Configuration
SNYK_TOKEN = os.environ.get("SNYK_TOKEN")
SNYK_REST_API_URL = os.environ.get("SNYK_REST_API_URL", "https://api.snyk.io/rest/")
SNYK_REST_API_VERSION = os.environ.get("SNYK_REST_API_VERSION", "2024-10-15")
SNYK_V1_API_URL = os.environ.get("SNYK_V1_API_URL", "https://api.snyk.io/v1/")

STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME")
STORAGE_QUEUE_NAME = os.environ.get("STORAGE_QUEUE_NAME")
BASE_TIMEOUT_MINUTES = int(os.environ.get("BASE_TIMEOUT_MINUTES", 30))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", 5))

# Logging setup
logging.getLogger("azure").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def get_queue_client() -> QueueClient:
    """Retrieves an Azure Storage Queue client with managed identity."""
    credential = DefaultAzureCredential()
    queue_url = f"https://{STORAGE_ACCOUNT_NAME}.queue.core.windows.net/"
    queue_name = STORAGE_QUEUE_NAME
    return QueueClient(queue_url, queue_name, credential=credential)


async def get_import_job_status(import_job_url):
    """
    Retrieves the import status of a Snyk import job.
    """
    async with aiohttp.ClientSession() as session:
        try:
            headers = {
                "Authorization": f"token {SNYK_TOKEN}",
            }
            async with session.get(import_job_url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("status")
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching import job status: {e}")
            return None


async def retrieve_project_ids(
    target_name: str, branch: str, org_id: str
) -> Optional[List[str]]:
    """Retrieves project IDs from Snyk API for a given target name."""
    url = f"{SNYK_REST_API_URL}orgs/{org_id}/projects"
    params = {
        "version": SNYK_REST_API_VERSION,
        "names_start_with": target_name,
        "target_reference": branch,
        "origins": "azure-repos",
        "limit": 100,
    }
    headers = {
        "Authorization": f"token {SNYK_TOKEN}",
    }
    full_url = f"{url}?{urlencode(params, safe="")}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                URL(full_url, encoded=True), headers=headers
            ) as response:
                response.raise_for_status()
                response_json = await response.json()
                data = response_json.get("data")
                project_ids = [item["id"] for item in data if item["type"] == "project"]
                return project_ids
        except aiohttp.ClientError as e:
            logger.error(f"Failed to retrieve project IDs: {e}")
            return None


async def tag_projects(
    project_ids: List[str], tags: List[Dict[str, str]], org_id: str
) -> bool:
    """Tags projects in Snyk with the given tags."""
    url = f"{SNYK_V1_API_URL}org/{org_id}/project/{{project_id}}/tags"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"token {SNYK_TOKEN}",
    }
    async with aiohttp.ClientSession() as session:
        for project_id in project_ids:
            full_url = url.format(project_id=project_id)
            for tag in tags:
                try:
                    async with session.post(
                        full_url,
                        headers=headers,
                        json=tag,
                    ) as response:
                        if response.status == 200:
                            logger.info(
                                f"Successfully tagged project {project_id} with {tag}"
                            )
                        elif response.status == 422:
                            logger.info(
                                f"Project {project_id} already tagged with {tag}."
                            )
                        else:
                            response.raise_for_status()
                except aiohttp.ClientError as e:
                    logger.error(f"Failed to tag project {project_id}: {e}")
                except Exception as e:
                    logger.exception(
                        f"An unexpected error occured while tagging project {project_id} with {tag}: {e}"
                    )
        return True


async def process_message(message, queue_client: QueueClient) -> None:
    """Processes a message from the queue."""
    try:
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

        import_status = await get_import_job_status(import_job_url)
        if import_status == "complete":
            logger.info(
                f"Import complete for target: {target_name}({branch}). Retrieving project IDs..."
            )
            project_ids = await retrieve_project_ids(target_name, branch, org_id)
            if project_ids:
                if await tag_projects(project_ids, tags, org_id):
                    await queue_client.delete_message(message)
                    logger.info(f"Message {message.id} processed successfully.")
            else:
                await queue_client.delete_message(message)
                logger.info(
                    f"No project IDs found for target: {target_name}({branch}), deleted {message.id}."
                )
        elif import_status == "pending":
            await requeue_message(message, queue_client, attempts)
            logger.info(
                f"Import staus pending for target: {target_name}({branch}), requeued message {message.id}"
            )
        else:
            await queue_client.delete_message(message)
            logger.error(
                f"Import failed for target: {target_name}({branch}), deleted message {message.id}"
            )

    except (json.JSONDecodeError, KeyError) as e:
        await queue_client.delete_message(message)
        logger.error(f"Invalid message format: {e}, deleted message {message.id}")
    except Exception as e:
        await requeue_message(message, queue_client, attempts)
        logger.error(
            f"An unexpected error occurred: {e}, requeued message {message.id}"
        )


async def requeue_message(message, queue_client: QueueClient, attempts: int) -> None:
    """Requeues a message with incremented attempts and logarithmic timeout."""
    attempts += 1
    content = json.loads(message.content)
    content["attempts"] = attempts

    # Calculate logarithmic timeout
    timeout_minutes = BASE_TIMEOUT_MINUTES * (0.5 ** (MAX_ATTEMPTS - attempts))
    timeout_seconds = int(timeout_minutes * 60)

    await queue_client.update_message(
        message, visibility_timeout=timeout_seconds, content=json.dumps(content)
    )
    logger.info(
        f"Message {message.id} requeued, Attempts: {attempts}, Timeout: {timeout_minutes:.2f} minutes"
    )


async def main() -> None:
    """Main function to run the message processing loop."""
    queue_client = await get_queue_client()
    while True:
        messages = queue_client.receive_messages(
            messages_per_page=32, visibility_timeout=30
        )
        tasks = []
        async for message in messages:
            task = asyncio.create_task(process_message(message, queue_client))
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(1)  # Polling interval


if __name__ == "__main__":
    asyncio.run(main())
