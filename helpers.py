# Bestand: helpers.py
# Functie: Bevat losse hulpfuncties zoals Azure Blob Storage uploads.

import os
import uuid
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

load_dotenv()

connect_str = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
container_name = os.environ.get('AZURE_CONTAINER_NAME')

def upload_to_blob(file):
    """Upload een bestand naar Azure Blob Storage en retourneer de URL."""
    if not file or file.filename == '':
        return None
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        filename = secure_filename(str(uuid.uuid4()) + "_" + file.filename)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=filename)
        blob_client.upload_blob(file)
        return blob_client.url
    except Exception as e:
        print(f"Azure Upload Error: {e}")
        return None