from django.shortcuts import render
import os
import time
import re
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from openai import OpenAI
import json
import logging

logger = logging.getLogger(__name__)

api_key = os.getenv('REACT_APP_OPENAI_API_KEY')
client = OpenAI(api_key=api_key)
assistant_id = None
vector_store_id = None
thread_id = None

def extract_email(user_input):
    email_regex = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    email_match = re.search(email_regex, user_input)
    return email_match.group(0) if email_match else None

def save_contact(user_input, new_thread_id):
    file_path = os.path.join(os.path.dirname(__file__), 'collected_data/customer_data.txt')
    email = extract_email(user_input)
    if not email:
        return

    try:
        with open(file_path, 'rb') as f:
            existing_data = f.read()
    except FileNotFoundError:
        existing_data = ''

    lines = existing_data.split('\n')
    email_exists = False
    existing_id = None

    for line in lines:
        if line.strip():
            stored_email, id = map(str.strip, line.split(': '))
            if stored_email == email:
                email_exists = True
                existing_id = id.replace(',', '')

    new_data = f"{email}: {new_thread_id}\n"

    if email_exists:
        global thread_id
        thread_id = existing_id
    else:
        with open(file_path, 'wb') as f:
            f.write(new_data)

def get_instructions():
    file_path = os.path.join(os.path.dirname(__file__), 'files/instructions.txt')
    try:
        with open(file_path, 'rb') as f:
            instructions = f.read()
    except FileNotFoundError:
        return HttpResponse(status=500, content=f"No instructions file found.")
    return str(instructions)

@csrf_exempt
def get_assistant(request):
    logger.debug("Fetching assistant")
    global assistant_id, vector_store_id

    try:
        file_path = os.path.join(os.path.dirname(__file__), 'files/knowledge.txt')
        with open(file_path, 'rb') as f:
            file = client.files.create(file=f, purpose="assistants")

        vector_store = client.beta.vector_stores.create(
            name="Josie Information",
            expires_after={"anchor": "last_active_at", "days": 7}
        )
        vector_store_id = vector_store.id
        logger.debug(vector_store_id)

        vector_store_file = client.beta.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file.id)
        vector_store_file_id = vector_store_file.id
        logger.debug(vector_store_file_id)

        POLL_INTERVAL = 1
        time.sleep(POLL_INTERVAL)
        logger.debug("Successfully created vector store")

        assistant = client.beta.assistants.create(
            name="Josie",
            instructions=get_instructions(),
            tools=[{"type": "file_search"}],
            model="gpt-4-1106-preview",
        )
        assistant_id = assistant.id

        client.beta.assistants.update(
            assistant_id,
            tool_resources={"file_search": {"vector_store_ids": [vector_store_id]}},
        )

        logger.debug("Assistant ID:", assistant_id)
        return JsonResponse({'assistant_id': assistant_id})

    except Exception as e:
        logger.error('Error fetching assistant: %s', str(e))
        return HttpResponse(status=500, content=f"An error occurred while creating assistant: {str(e)}")

@csrf_exempt
def send_message(request):
    global assistant_id, thread_id

    data = json.loads(request.body)
    user_input = data.get('input')

    try:
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_input,
        )

        if '@' in user_input:
            save_contact(user_input, thread_id)

        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)

        while True:
            response = client.beta.threads.runs.retrieve(run_id=run.id, thread_id=thread_id)
            if response.status not in ["in_progress", "queued"]:
                break
            time.sleep(2)

        message_list = client.beta.threads.messages.list(thread_id)
        last_message = next((msg for msg in message_list.data if msg.run_id == run.id and msg.role == 'assistant'), None)

        if last_message:
            return JsonResponse({'response': last_message.content[0].text.value.replace('【0:knowledge.txt†source】', '')})
        else:
            return HttpResponse(status=500, content='No response from the assistant.')

    except Exception as e:
        logger.error('Error retrieving response: %s', str(e))
        return HttpResponse(status=500, content=f"An error occurred while retrieving response: {str(e)}")
