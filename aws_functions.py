
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import unicodedata
import os
from datetime import datetime
import pytz
import aioboto3
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

def emit_log_to_s3(record,log_file='file_records.log'):
    s3_client = boto3.client('s3')
    try:
        # Get existing log content from S3 (if it exists)
        try:
            existing_log = s3_client.get_object(Bucket=os.getenv("AWS_BUCKET_NAME"), Key=f"{os.getenv('PROJECT_NAME')}/{os.getenv('SERVER_LEVEL')}/{log_file}")
            existing_content = existing_log['Body'].read().decode('utf-8')
        except s3_client.exceptions.NoSuchKey:
            existing_content = ""

        # Prepare new log entry
        indian_timezone = pytz.timezone('Asia/Kolkata')
        current_time = datetime.now(indian_timezone)
        log_entry = f"{current_time.strftime('%Y-%m-%d %H:%M:%S')} - {record}"

        # Append new entry to existing log content
        updated_content = existing_content + log_entry + "\n"

        # Upload back to S3
        s3_client.put_object(
            Bucket=os.getenv("AWS_BUCKET_NAME"),
            Key=f"{os.getenv('PROJECT_NAME')}/{os.getenv('SERVER_LEVEL')}/{current_time.strftime('%Y-%m-%d')}/{log_file}",
            Body=updated_content.encode('utf-8')
        )
    except (NoCredentialsError, ClientError) as e:
        print(f"Error uploading log to S3: {e}")

def get_logs_from_s3(local_folder=os.getcwd(),project_name=os.getenv('PROJECT_NAME'),server_level=os.getenv('SERVER_LEVEL'), current_time=datetime.now(pytz.timezone('Asia/Kolkata'))):
    folder_prefix = f"{project_name}/{server_level}/{current_time.strftime('%Y-%m-%d')}/"
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    s3 = boto3.client('s3')
    os.makedirs(local_folder,exist_ok=True)
    try:
        # List objects in the S3 bucket
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=folder_prefix)
        
        # Download filtered files
        for page in page_iterator:
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                if os.path.splitext(key)[1] == '':
                    continue
                
                # Construct local file path
                local_path = os.path.join(local_folder, key)
                local_path = unicodedata.normalize('NFC',local_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                try:
                    print(f"Downloading: {key} -> {local_path}")
                    s3.download_file(bucket_name, key, local_path)
                    print(f"Downloaded: {local_path}")
                except Exception as e:
                    print(f"Error downloading {key}: {e}")
    except NoCredentialsError as e:
        print("No AWS credentials found.")
        raise e
    except Exception as e:
        print(f"An error occurred: {e}")
        raise e                    

def normalize_directory(local_dir):
    for root, _, files in os.walk(local_dir):
        for file in files:
            source_file_path = os.path.join(root,file)
            destination_file_path = unicodedata.normalize('NFC',source_file_path)
            os.rename(source_file_path, destination_file_path)

def upload_folder_to_s3(local_dir, prefix=''):
    start_time = datetime.now()
    print(f"{start_time.strftime('%Y-%m-%d %H:%M:%S:%MS')} - Uploading files to S3 {prefix} from local folder {local_dir}")
    s3_bucket = os.getenv("AWS_BUCKET_NAME")
    s3_client = boto3.client('s3')
    normalize_directory(local_dir=local_dir)
    for root, dirs, files in os.walk(local_dir):
        for dir in dirs:
            dir_path = os.path.join(root, dir)
            relative_path = os.path.relpath(dir_path, local_dir)
            
            # Create the directory in S3 if it doesn't exist
            try:
                save_path = os.path.join(prefix, relative_path)
                save_path = unicodedata.normalize('NFC',save_path)
                s3_client.put_object(Bucket=s3_bucket, Key=save_path)
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    continue  # Directory already exists
                else:
                    raise
            
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, local_dir)
            
            try:
                save_path = os.path.join(prefix, relative_path)
                save_path = unicodedata.normalize('NFC',save_path)
                s3_client.upload_file(file_path, s3_bucket, save_path)
                print(f"Uploaded: {file_path} -> s3://{s3_bucket}/{save_path}")
                # emit_log_to_s3(f"Uploaded: {file_path} -> s3://{s3_bucket}/{save_path}")
                end_time = datetime.now()
                print(f"{end_time.strftime('%Y-%m-%d %H:%M:%S:%MS')} - Uploaded files to S3 {prefix} from local folder {local_dir} within {(end_time - start_time).total_seconds()} seconds")
            except Exception as e:
                print(f"Error uploading {file_path}: {e}")
                raise e

async def upload_file_to_s3_async(s3_client, bucket_name, file_path, s3_key):
    try:
        # Offload the potentially blocking call to a thread
        await s3_client.upload_file(file_path, bucket_name, s3_key)
        print(f"Uploaded {file_path} to s3://{bucket_name}/{s3_key}")
    except Exception as e:
        print(f"Error uploading {file_path}: {e}")

async def upload_folder_to_s3_async(folder_path, s3_prefix):
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    async with aioboto3.Session().client('s3') as s3_client:
        tasks = []
        for root, dirs, files in os.walk(folder_path):
            for file_name in files:
                local_file_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(local_file_path, folder_path)
                s3_key = os.path.join(s3_prefix, relative_path).replace("\\", "/")
                # Schedule each upload as a task
                task = asyncio.create_task(
                    upload_file_to_s3_async(s3_client, bucket_name, local_file_path, s3_key)
                )
                tasks.append(task)
        # Await all tasks concurrently
        await asyncio.gather(*tasks)
    return 0

# async def upload_file_to_s3_async(s3_client, bucket_name, file_path, s3_key):
#     try:
#         await s3_client.upload_file(file_path, bucket_name, s3_key)
#         print(f"Uploaded {file_path} to s3://{bucket_name}/{s3_key}")
#     except Exception as e:
#         print(f"Error uploading {file_path}: {e}")

# async def upload_folder_to_s3_async(folder_path, s3_prefix):
#     bucket_name = os.getenv("AWS_BUCKET_NAME")
#     async with aioboto3.Session().client('s3') as s3_client:
#         for root, dirs, files in os.walk(folder_path):
#             for file_name in files:
#                 local_file_path = os.path.join(root, file_name)

#                 relative_path = os.path.relpath(local_file_path, folder_path)
#                 s3_key = os.path.join(s3_prefix, relative_path).replace("\\", "/")

#                 await upload_file_to_s3_async(s3_client, bucket_name, local_file_path, s3_key)
#     return 0

async def upload_file_to_s3(bucket_name, path, s3_key):
    session = aioboto3.Session()
    staging_path = Path(path)
    async with session.client("s3") as s3:
        try:
            with staging_path.open("rb") as fp:
                await s3.upload_fileobj(fp, bucket_name, s3_key)
                print("Uploaded file to S3")
        except Exception as e:
            print("File upload failed")
            raise e

async def download_file_from_s3(bucket_name, path, temp_dir):
    filename = os.path.basename(path)
    download_path = os.path.join(temp_dir, filename)
    async with aioboto3.Session().client('s3') as s3:
        try:
            await s3.download_file(bucket_name, path, download_path)
            print('file downloaded')
        except Exception as e:
            raise e


# async def list_s3_objects_async(s3_client, bucket_name, prefix):
#     """List all objects in the specified S3 folder (prefix)."""
#     continuation_token = None
#     while True:
#         list_kwargs = {
#             'Bucket': bucket_name,
#             'Prefix': prefix,
#         }
#         if continuation_token:
#             list_kwargs['ContinuationToken'] = continuation_token

#         response = await s3_client.list_objects_v2(**list_kwargs)
#         if 'Contents' not in response:
#             break

#         for obj in response['Contents']:
#             yield obj['Key']

#         if not response.get('IsTruncated'):
#             break
#         continuation_token = response.get('NextContinuationToken')

# async def download_file_from_s3_async(s3_client, bucket_name, s3_key, local_path):
#     try:
#         os.makedirs(os.path.dirname(local_path), exist_ok=True)

#         await s3_client.download_file(bucket_name, s3_key, local_path)
#         print(f"Downloaded s3://{bucket_name}/{s3_key} to {local_path}")
#     except Exception as e:
#         print(f"Error downloading {s3_key}: {e}")


# async def download_folder_from_s3_async(local_folder, folder_prefix):
#     bucket_name = os.getenv("AWS_BUCKET_NAME")
#     async with aioboto3.Session().client('s3') as s3_client:
#         async for s3_key in list_s3_objects_async(s3_client, bucket_name, folder_prefix):
#             local_path = os.path.join(local_folder, s3_key)
#             local_path = unicodedata.normalize('NFC',local_path)

#             await download_file_from_s3_async(s3_client, bucket_name, s3_key, local_path)
#     return 0

async def list_s3_objects_async(s3_client, bucket_name, prefix):
    """List all objects in the specified S3 folder (prefix)."""
    continuation_token = None
    while True:
        list_kwargs = {
            'Bucket': bucket_name,
            'Prefix': prefix,
        }
        if continuation_token:
            list_kwargs['ContinuationToken'] = continuation_token

        response = await s3_client.list_objects_v2(**list_kwargs)
        if 'Contents' not in response:
            break

        for obj in response['Contents']:
            yield obj['Key']

        if not response.get('IsTruncated'):
            break
        continuation_token = response.get('NextContinuationToken')


async def download_file_from_s3_async(s3_client, bucket_name, s3_key, local_path):
    """Download a single file from S3 to a local path."""
    try:
        # Ensure the local directory exists.
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # Offload the blocking download to a separate thread.
        await s3_client.download_file(bucket_name, s3_key, local_path)
        print(f"Downloaded s3://{bucket_name}/{s3_key} to {local_path}")
    except Exception as e:
        print(f"Error downloading {s3_key}: {e}")


async def download_folder_from_s3_async(local_folder, folder_prefix):
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    tasks = []
    # Use a single client session for all operations.
    async with aioboto3.Session().client('s3') as s3_client:
        # Iterate over all S3 keys in the folder prefix.
        async for s3_key in list_s3_objects_async(s3_client, bucket_name, folder_prefix):
            # Build the local path for the file.
            local_path = os.path.join(local_folder, s3_key)
            local_path = unicodedata.normalize('NFC', local_path)

            # Create a task for each download.
            task = asyncio.create_task(
                download_file_from_s3_async(s3_client, bucket_name, s3_key, local_path)
            )
            tasks.append(task)
        # Wait for all download tasks to complete concurrently.
        if tasks:
            await asyncio.gather(*tasks)
    return 0

def download_folder_from_s3(local_folder, folder_prefix=''):
    start_time = datetime.now()
    print(f"{start_time.strftime('%Y-%m-%d %H:%M:%S:%MS')} - Downloading files from S3 {folder_prefix} to local folder {local_folder}")
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    s3 = boto3.client('s3')
    os.makedirs(local_folder,exist_ok=True)
    try:
        # List objects in the S3 bucket
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=folder_prefix)
        
        # Download filtered files
        for page in page_iterator:
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                if os.path.splitext(key)[1] == '':
                    continue
                
                # Construct local file path
                local_path = os.path.join(local_folder, key)
                local_path = unicodedata.normalize('NFC',local_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                try:
                    print(f"Downloading: {key} -> {local_path}")
                    s3.download_file(bucket_name, key, local_path)
                    # emit_log_to_s3(f"Downloaded: {key} -> {local_path}")
                    print(f"Downloaded: {local_path}")
                except Exception as e:
                    print(f"Error downloading {key}: {e}")
        
        end_time = datetime.now()
        print(f"{end_time.strftime('%Y-%m-%d %H:%M:%S:%MS')} - Downloaded files from S3 {folder_prefix} to local folder {local_folder} within {(end_time - start_time).total_seconds()} seconds")
    
    except NoCredentialsError as e:
        print("No AWS credentials found.")
        raise e
    except Exception as e:
        print(f"An error occurred: {e}")
        raise e

def check_file_exists_in_s3(file_path):
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    s3_client = boto3.client('s3')
    
    try:
        s3_client.head_object(Bucket=bucket_name, Key=unicodedata.normalize('NFC',file_path))
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=os.getenv('FOLDER_PREFIX'))
            for page in page_iterator:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if unicodedata.normalize('NFC',key) == unicodedata.normalize('NFC',file_path):
                        return True
            return False
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            raise e

def verify_vectorstore_exists(vectorstore_path):
    if not check_file_exists_in_s3(os.path.join(vectorstore_path,'index.faiss')):
        return False
    elif not check_file_exists_in_s3(os.path.join(vectorstore_path,'index.pkl')):
        return False
    else:
        return True

def download_files_from_s3(local_folder, file_path_list):
    s3 = boto3.client('s3')
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    folder_prefix = ''
    file_path_list = [unicodedata.normalize('NFC',file_path) for file_path in file_path_list]
    try:
        # List objects in the S3 bucket
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=folder_prefix)
        
        # Download filtered files
        for page in page_iterator:
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                # Apply file filter if specified
                if key not in file_path_list:
                    if unicodedata.normalize('NFC',key) not in file_path_list:
                        continue

                # Construct local file path
                local_path = os.path.join(local_folder, key)
                local_path = unicodedata.normalize('NFC',local_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                try:
                    print(f"Downloading: {key} -> {local_path}")
                    s3.download_file(bucket_name, key, local_path)
                    print(f"Downloaded: {local_path}")
                    # emit_log_to_s3(f"Downloaded: {key} -> {local_path}")
                except Exception as e:
                    print(f"Error downloading {key}: {e}")
    
    except NoCredentialsError as e:
        print("No AWS credentials found.")
        raise e
    except Exception as e:
        print(f"An error occurred: {e}")
        raise e

def delete_s3_folder(folder_path):
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    s3_client = boto3.client('s3')
    
    try:
        # List objects in the S3 bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=folder_path)
        
        # Delete objects within the folder_path
        delete_keys = {'Objects': []}
        for page in page_iterator:
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                # Construct the full key for deletion
                delete_key = {'Key': key}
                delete_keys['Objects'].append(delete_key)
                
                print(f"Deleting: {key}")
        
        # Perform batch delete operation
        if len(delete_keys['Objects']) > 0:
            s3_client.delete_objects(Bucket=bucket_name, Delete=delete_keys)
            print(f"Deleted {len(delete_keys['Objects'])} objects in folder '{folder_path}'")
            # emit_log_to_s3('Deleted {} objects in folder {}'.format(", ".join([obj.get('Key') for obj in delete_keys['Objects']]), folder_path))
        else:
            print(f"No objects found in folder '{folder_path}'")
            # emit_log_to_s3('Failed to delete objects in folder {}. No objects found'.format(folder_path))

    except ClientError as e:
        print(f"An error occurred: {e}")

def list_s3_objects(prefix='', bucket_name=os.getenv("AWS_BUCKET_NAME")):
    # bucket_name = os.getenv("AWS_BUCKET_NAME")
    s3_client = boto3.client('s3')
    
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        for page in page_iterator:
            for obj in page.get('Contents', []):
                print(f"Key: {obj['Key']}")
                print(f"Size: {obj['Size']} bytes")
                print(f"Last Modified: {obj['LastModified']}")
                print(f"ETag: {obj['ETag']}")
                print(f"File Extension: {os.path.splitext(obj['Key'])[-1]}")
                print("---")
    
    except ClientError as e:
        print(f"An error occurred: {e}")
        
def create_presigned_url(key, bucket_name=os.getenv("AWS_BUCKET_NAME"), expiration=3600):
    """Generate a presigned URL to share an S3 object
    
    :param bucket_name: string
    :param object_name: string
    :param expiration: Time in seconds for the presigned URL to remain valid
    :return: Presigned URL as string. If error, returns None.
    """
    
    # Create an S3 client
    s3_client = boto3.client('s3')
    
    try:
        # Generate a presigned URL for the S3 object
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=expiration
        )
    except ClientError as e:
        print(f"An error occurred: {e}")
        return None
    
    # The response contains the presigned URL
    return url

def create_presigned_url_for_file(file_path, prefix='PDFs', bucket_name=os.getenv("AWS_BUCKET_NAME"), expiration=3600):
    s3_bucket = os.getenv("AWS_BUCKET_NAME")
    s3_client = boto3.client('s3')
    save_path = os.path.join(prefix, unicodedata.normalize('NFC',os.path.basename(file_path)))
    key = unicodedata.normalize('NFC',save_path)
    s3_client.upload_file(file_path, s3_bucket, key)
    
    return create_presigned_url(key, bucket_name, expiration)