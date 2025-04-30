![snyk-oss-category](https://github.com/snyk-labs/oss-images/blob/main/oss-community.jpg)

# Snyk Tag Processor

## Description

This application monitors an Azure Storage Queue for messages containing information about newly imported Snyk projects from Azure Repos. Upon receiving a message, it polls the Snyk API to check the import job status. Once the import is complete, it retrieves the IDs of the newly created Snyk projects based on the target name and branch specified in the message. Finally, it applies a predefined set of tags to these projects using the Snyk API. This tool automates the process of tagging Snyk projects immediately after they are imported, ensuring consistency and facilitating better organization within the Snyk platform.

## Table of Contents

- [Installation and Setup](#installation-and-setup)
  - [Prerequisites](#prerequisites)
  - [Environment Setup](#environment-setup)
    - [Virtual Environment Setup](#virtual-environment-setup)
    - [Environment Variables](#environment-variables)
  - [Azure Authentication](#azure-authentication)
    - [Authenication Methods](#authentication-methods)
      - [Managed Identity](#managed-identity)
      - [Environment Variables](#environment-variables)
      - [Azure CLI](#azure-cli)
  - [Installation Methods](#installation-methods)
    - [Development/Local Installation](#developmentlocal-installation)
      - [Direct Installation](#direct-installation)
      - [Docker](#docker)
    - [Deployment/Production Installation](#deploymentproduction-installation)
      - [Container Deployment (Docker)](#container-deployment-docker)
  - [Verification](#verification)
- [Usage](#usage)
  - [Message Format](#message-format)
  - [Workflow](#workflow)
- [Features](#features)

# Installation and Setup

This section guides you through the necessary steps to set up and install the application.

## Prerequisites

Before installing, ensure you have the following:

- **Python**: Version 3.9 or higher installed on your system.
- **pip**: The Python package installer, which usually comes bundled with Python. You can check if you have it by running pip --version in your terminal.
- **Azure CLI** (Optional but Recommended): For interacting with Azure services, especially if you plan to use managed identities.
- **Docker** (Optional): If you prefer to run the application in a containerized environment.
- **Azure Storage Account and Queue**: You need an active Azure Storage Account and a Queue within it to receive messages.
- **Snyk API Token**: You need a Snyk API token with the necessary permissions to read project information and apply tags. You can generate a token at the group level or orginization level under "Settings"->"Service Accounts".

## Environment Setup

This subsection guides you through setting up your development environment.

### Virtual Environment Setup

It's highly recommended to create a virtual environment to isolate the project's dependencies.

**Using venv (pre-installed with Python)**:

```
python -m venv .venv
```

_On macOS/Linux_

```
source .venv/bin/activate
```

_On Windows_

```
.venv\Scripts\activate
```

\
**Using conda (if you have Anaconda installed)**:

```
conda create --name snyk-tag-processor python=3.13
conda activate snyk-tag-processor
```

### Environment Variables

| Variable Name                    | Required | Default Value               | Type    | Description                                                                                                      |
| :------------------------------- | :------- | :-------------------------- | :------ | :--------------------------------------------------------------------------------------------------------------- |
| `SNYK_TOKEN`                     | true     | None                        | String  | Your Snyk API token.                                                                                             |
| `STORAGE_ACCOUNT_NAME`           | true     | None                        | String  | The name of your Azure Storage Account that hosts the queue.                                                     |
| `STORAGE_QUEUE_NAME`             | true     | None                        | String  | The name of the Azure Storage Queue to monitor for import messages.                                              |
| `SNYK_REST_API_URL`              | false    | `https://api.snyk.io/rest/` | String  | The base URL for the Snyk REST API. You might need to change this for specific Snyk environments.                |
| `SNYK_REST_API_VERSION`          | false    | `2024-10-15`                | String  | The version of the Snyk REST API to use.                                                                         |
| `SNYK_V1_API_URL`                | false    | `https://api.snyk.io/v1/`   | String  | The base URL for the Snyk v1 API, which is used for tagging projects.                                            |
| `MAX_TIMEOUT_MINUTES`            | false    | `30`                        | Integer | The maximum time in minutes to spend processing a single message before considering it failed.                   |
| `MAX_ATTEMPTS`                   | false    | `5`                         | Integer | The maximum number of times to attempt processing a message before deleting it from the queue.                   |
| `QUEUE_POLLING_INTERVAL_SECONDS` | false    | `10`                        | Float   | The interval in seconds to poll the Azure Storage Queue for new messages.                                        |
| `VISIBILITY_TIMEOUT_SECONDS`     | false    | `30`                        | Integer | The initial visibility timeout in seconds for messages retrieved from the queue, and the lease renewal interval. |

## Azure Authentication

The Snyk Tag Processor uses the `DefaultAzureCredential` class from the `azure-identity` library to authenticate with Azure services.

https://learn.microsoft.com/en-us/dotnet/api/azure.identity.defaultazurecredential?view=azure-dotnet

DefaultAzureCredential attempts to obtain Azure credentials from the following sources in order:

1. Environment variables: Credentials provided through environment variables.
2. Managed identity: Credentials managed by Azure for resources like VMs, Container Apps, etc. (Recommended in Azure).
3. Azure CLI: Credentials from a logged-in Azure CLI session (Suitable for local development, not production).
4. Other methods: (Service Principal, etc.)

### Authentication Methods

#### Managed Identity

1. Enable Managed Identity for the Azure resource hosting the Snyk Tag Processor (e.g., Azure Container App).

2. Grant the Managed Identity the Storage Queue Data Contributor role on the Azure Storage Queue.

3. DefaultAzureCredential will automatically use the Managed Identity.

**Note**: This is the recommended approach for production deployments in Azure due to its security benefits.

#### Environment Variables

Set the following environment variables:

**AZURE_CLIENT_ID**: The client ID of the service principal or user-assigned managed identity.\
**AZURE_TENANT_ID**: The tenant ID of your Azure subscription.\
**AZURE_CLIENT_SECRET**: (For service principals) The client secret.

Use this method when managed identity is not available or for specific testing scenarios.

**Warning**: Handle these environment variables securely. Do not hardcode them.

#### Azure CLI

Ensure the Azure CLI is installed and you are logged in with an account that has access to the Azure Storage Queue.

DefaultAzureCredential will use the logged-in user's credentials.

**Warning**: This method is generally not recommended for production as it depends on an active user session.

## Installation Methods

Choose one of the following methods to install the application:

### Development/Local Installation

#### Direct Installation

1. Clone the repository

**Warning:** The following installation instructions are intended for local development and testing only. Do not use these instructions for production deployments. See the [Deployment/Production Installation](#deploymentproduction-installation) section for production-ready deployment methods.

```
git clone https://github.com/snyk-ps/snyk-tag-processor.git
cd snyk-tag-processor
```

2. Create and activate a virtual environment (as described in Environment Setup).
3. Install the required dependencies:

```
pip install -r requirements.txt
```

#### Docker

1. Clone the repository

```
git clone https://github.com/snyk-ps/snyk-tag-processor.git
cd snyk-tag-processor
```

2. Build the Docker image:

```
docker build -t snyk-tag-processor .
```

3. Run the Docker container, making sure to pass the necessary environment variables:

```
docker run -d --name snyk-tag-processor \
    -e SNYK_TOKEN="${SNYK_TOKEN}" \
    -e STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME}" \
    -e STORAGE_QUEUE_NAME="${STORAGE_QUEUE_NAME}" \
    # Add other optional environment variables if needed
    snyk-tag-processor
```

Replace the placeholder values with your actual environment variables.

### Deployment/Production Installation

This section provides instructions for deploying the Snyk Tag Processor in a production environment.

#### Container Deployment (Docker)

1.  **Obtain the Image:**
    - Navigate to the [Packages](https://github.com/snyk-ps/snyk-tag-processor/pkgs/container/snyk-tag-processor) section of the GitHub repository
    - Locate the desired version of the application.
    - Follow the instructions on the package page to pull the image.
2.  **Run the Container:**

    - Use the `docker run` command to start the container, providing the necessary environment variables.

    ```bash
    docker run -d --name snyk-tag-processor \
        -e SNYK_TOKEN="${SNYK_TOKEN}" \
        -e STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME}" \
        -e STORAGE_QUEUE_NAME="${STORAGE_QUEUE_NAME}" \
        # Add other optional environment variables as needed
        your-image-name:your-image-tag
    ```

    - Replace `your-image-name:your-image-tag` with the actual name and tag of the image you pulled.
    - It is strongly recommended to use a secret store for the `SNYK_TOKEN`. For instructions on managing this with Azure Container Apps see the docs below: <https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets?tabs=azure-portal#using-secrets>

**Container Requirements:**

- **Compute Resources:** A minimum of 1 CPU core and 512MB of memory is recommended. Adjust resources based on your queue volume and processing load. Monitor your container's resource usage and scale accordingly.
- **Storage:** The Snyk Tag Processor container does not require persistent storage. However, ensure sufficient temporary storage for logs.
- **Network:** The container must have network connectivity to:
  - The Azure Storage Queue. Ensure appropriate firewall rules or network policies are configured.
  - The Snyk API server (api.snyk.io, port 443)

**Redundancy:**

While not required, you may run multiple replicas of the Snyk Tag Processor container to ensure high availability and fault tolerance. If one container instance fails, others can continue processing messages.

Consider using container orchestration platforms like:

- **Kubernetes:** For advanced orchestration and scaling.
- **Azure Container Apps:** For a serverless container platform that simplifies deployment and scaling in Azure.

### Verification

After installation, you can verify that the application is set up correctly by:

- Checking Environment Variables: Ensure all the required environment variables are set correctly. The application will log an error and exit if any required variable is missing.
- Running the Application (for direct installation):
  1. Activate your virtual environment.
  2. Navigate to the src directory.
  3. Run the application:
  ```
  python main.py
  ```
  4. Observe the logs for any initial setup errors. The application will start polling the Azure Storage Queue if the setup is successful.
- Checking Docker Container Logs (for Docker installation):

  ```
  docker logs snyk-tag-processor
  ```

  Look for any error messages during the container startup.

## Usage

The Snyk Tag Processor automates the process of applying tags to your Snyk projects after they are imported from Azure Repos. This allows you to organize and categorize your Snyk projects based on your specific needs, such as application ID, team ownership, or environment.

### Message Format

To use the Snyk Tag Processor, your Azure Storage Queue needs to be populated with messages in the following JSON format:

```
{
  "target_name": "your-repo-name",
  "branch": "main",
  "tags": [
    { "key": "tag_name_1", "value": "tag_value_1" },
    { "key": "tag_name_2", "value": "tag_value_2" }
  ],
  "org_id": "your-snyk-organization-id",
  "import_job_url": "https://api.snyk.io/rest/orgs/your-snyk-organization-id/imports/your-import-job-id"
}
```

**target_name**: The name of the Azure Repos repository that was imported.\
**branch**: The branch of the repository that was imported.\
**tags**: An array of tag objects, each with a "key" and a "value". These tags will be applied to the Snyk projects found for the given target and branch.\
**org_id**: The ID of your Snyk organization where the projects were imported.\
**import_job_url**: The URL of the Snyk import job that created the projects. This is used to check the import status.

### Workflow

**Message Reception**: The application continuously polls the configured Azure Storage Queue for new messages.

**Message Processing**: When a message is received, the application extracts the information from the JSON payload.

**Import Status Check**: The application uses the import job URL to query the Snyk API for the status of the import job.

**Project Identification**: Once the import job is complete, the application uses the target name (repo name) and branch to retrieve the IDs of the Snyk projects that were created as a result of the import.

**Tag Application**: The application iterates through the list of tags in the message and applies each tag to the identified Snyk projects using the Snyk API.

**Message Deletion**:
Messages are deleted if any of the following conditions are met:

1. If the tagging is successful.
2. No projects are found for the given target and branch.
3. The maximum number of retry attempts for the message has been reached.

**Message Requeueing**: Messages are requeued if any of the following conditions are met:

1. The import job status is "pending".
2. An unexpected error occurs during processing (e.g., network error, Snyk API error).
3. Tagging of one or more projects fails.
4. The target fails to import.

## Features

- **Automated Tagging**: Automatically applies predefined tags to newly imported Snyk projects.

- **Queue-Based Processing**: Utilizes an Azure Storage Queue to decouple the project import process from the tagging process.

- **Import Status Polling**: Regularly checks the status of the Snyk import job to ensure projects are fully created before attempting to tag them.

- **Retry Mechanism**: Implements a retry mechanism for pending import jobs or transient errors during API calls or queue operations.

- **Configurable**: Offers various configuration options via environment variables, allowing for customized behavior

- **Detailed Logging**: Provides comprehensive logging of application activity, including successful tagging, errors, and retries.
