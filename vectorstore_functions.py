from datetime import datetime
import os
import aiohttp
import aiofiles
import asyncio
import unicodedata
import anthropic
from langchain_core.documents import Document
from llama_parse import LlamaParse
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores.faiss import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter
from concurrent.futures import ThreadPoolExecutor
from constants import EMBEDDINGS_MODEL_NAME
from prompts import TITLE_GENERATION_PROMPT, GEN_QA_PROMPT, TRANSLATE_PROMPT, DOCUMENT_DESCRIBE_PROMPT

async def llm_invoke(prompt):
    client = anthropic.Anthropic()
    model = "claude-3-haiku-20240307"
    max_tokens = 1000
    temperature = 0
    start_time = datetime.now()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ]
    )
    end_time = datetime.now()
    elapsed_time = (end_time - start_time).total_seconds()
    cost = ((message.usage.input_tokens * 0.25) + (message.usage.output_tokens * 1.25))/1000000
    print(f"Anthropic API CALL:\nModel: claude-3-haiku-20240307\nElapsed time: {elapsed_time}\nInput tokens: {message.usage.input_tokens}\nOutput tokens: {message.usage.output_tokens}\nCost: {cost}")
    # record = f"FILE MANAGER LLM API CALL ELAPSED TIME: {elapsed_time} INPUT_TOKENS: {message.usage.input_tokens} OUTPUT_TOKENS: {message.usage.output_tokens} COST: {cost}\nPROMPT:\n{prompt}\n\nRESPONSE:\n{message.content[0].text}\n\n{'-'*50}\n\n"
    # emit_log_to_s3(record=record, log_file="file_manager_llm_api_calls.log")
    return message.content[0].text, cost

def get_summary_concurrent(document_chunks):
    cost = 0.0
    summary_parts = []
    
    def process_chunk(chunk):
        """Process individual chunk and return formatted summary with cost"""
        llm_response, chunk_cost = llm_invoke(DOCUMENT_DESCRIBE_PROMPT.format(DOCUMENT_TEXT=chunk))
        if "<summary>" in llm_response:
            llm_response = llm_response.split("<summary>")[1].split("</summary>")[0].strip()
        return llm_response, chunk_cost

    with ThreadPoolExecutor() as executor:
        # Submit all chunks for parallel processing
        futures = [executor.submit(process_chunk, chunk) for chunk in document_chunks]
        
        # Collect results in order
        for future in futures:
            llm_response, chunk_cost = future.result()
            summary_parts.append(llm_response)
            cost += chunk_cost

    # Join all summary parts with newlines
    return "\n".join(summary_parts), cost

async def get_chunks(markdown_text, max_token_length=512, chunk_overlap=32):
    try:
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="cl100k_base", chunk_size=max_token_length, chunk_overlap=chunk_overlap)
        return text_splitter.split_text(markdown_text)
    except Exception as e:
        raise Exception(f"get_chunks --> {str(e)}")

async def download_file_async(file_path, tmp_dir, file_s3_url):
    """
    Download a file asynchronously from a given URL and save it to the local path.

    Parameters:
      - file_path: Relative file path (used to create subdirectories in tmp_dir).
      - tmp_dir: The root directory where the file should be saved.
      - file_s3_url: The URL to download the file from.
    
    Returns:
      - 0 if the download succeeded, 1 otherwise.
    """
    save_file_path = os.path.join(tmp_dir, file_path)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(file_s3_url) as response:
                if response.status == 200:
                    # Ensure the directory exists
                    os.makedirs(os.path.dirname(save_file_path), exist_ok=True)
                    # Use aiofiles for asynchronous file I/O
                    async with aiofiles.open(save_file_path, 'wb') as file:
                        while True:
                            chunk = await response.content.read(1024)  # 1 KB chunks
                            if not chunk:
                                break
                            await file.write(chunk)
                    print(f"File downloaded successfully to {save_file_path}")
                    return 0
                else:
                    print(f"Failed to download file. Status code: {response.status}")
                    return 1
        except Exception as e:
            print(f"Error downloading file: {e}")
            return 1

async def async_download_file_s3_url(save_file_path, file_s3_url):
    try:
        print("Starting Download to file: ", save_file_path)
        async with aiohttp.ClientSession() as session:
            async with session.get(file_s3_url) as response:
                if response.status == 200:
                    # Ensure the directory exists
                    os.makedirs(os.path.dirname(save_file_path), exist_ok=True)
                    # Use aiofiles for asynchronous file I/O
                    async with aiofiles.open(save_file_path, 'wb') as file:
                        while True:
                            chunk = await response.content.read(1024)  # 1 KB chunks
                            if not chunk:
                                break
                            await file.write(chunk)
                    print(f"File downloaded successfully to {save_file_path}")
                else:
                    raise Exception(f"Status code: {response.status}. URL: \n{file_s3_url}")
    except Exception as e:
        raise Exception(f"async_download_file_s3_url --> {str(e)}")

async def llama_parse_file(file_path):
    print("In llama parse")
    print(file_path)
    try:
        api_key = os.environ['LLAMA_CLOUD_API_KEY']
        parser = LlamaParse(api_key=api_key)
        # read_file_path = os.path.join(temp_directory, file_path)
        # await async_download_file_s3_url(save_file_path=read_file_path, file_s3_url=file_s3_url)
        print("parsing the file")
        start_time = datetime.now()
        # json_data = parser.get_json_result(read_file_path)
        json_data = await parser.aget_json(file_path)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        if json_data == []:
            raise Exception("Llama Parse of the file failed")
            # with open("markdown.md", "r") as f:
            #     markdown_data_text = f.read()
            #     documents_list = [Document(page_content=markdown_data_text, metadata = {'pages': [1]})]
            # return documents_list, markdown_data_text, llama_parse_cost(num_pages=1, cost_per_page=0.003, total_cost=0.003, run_time=elapsed_time)
        markdown_data_pages = ""
        documents_list = []
        for page in json_data[0]['pages']:
            markdown_data_pages = markdown_data_pages + "\n\n" + page['md'] + "\n\n" + "<!-- page: " + str(page['page']) + " -->"
            document = Document(page_content=page['md'], metadata={'pages': [page['page']]})
            documents_list.append(document)
        markdown_data_text = "\n\n".join([markdown_text['md'] for markdown_text in json_data[0]['pages']])
        
        # Cost calculation
        # llama_parse_api_cost = llama_parse_cost(num_pages=len(json_data[0]['pages']), cost_per_page=0.003, total_cost=len(json_data[0]['pages']) * 0.003, run_time=elapsed_time)
        return documents_list, markdown_data_text
    except Exception as e:
        raise Exception("llama_parse_file --> "+str(e))

async def get_description_concurrent(document_chunks):
    try:
        """Get descriptions concurrently using async operations."""
        loop = asyncio.get_running_loop()
        
        async def process_chunk(chunk: str):
            try:
                """Process individual chunk asynchronously."""
                llm_response, chunk_cost = await llm_invoke(
                    DOCUMENT_DESCRIBE_PROMPT.format(DOCUMENT_TEXT=chunk)
                )
                if "<summary>" in llm_response:
                    llm_response = llm_response.split("<summary>")[1].split("</summary>")[0].strip()
                return llm_response, chunk_cost
            except Exception as e:
                raise Exception("process_chunks --> " + str(e))
        
        start_time = datetime.now()
        
        # Create tasks
        tasks = [
            asyncio.create_task(process_chunk(chunk))
            for chunk in document_chunks
        ]
        
        # Gather results
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Combine results
        summary_parts = []
        cost_list = []
        
        for result in results:
            if isinstance(result, Exception):
                raise result
            llm_response, chunk_cost = result
            summary_parts.append(llm_response)
            cost_list.append(chunk_cost)
        
        elapsed_time = (datetime.now() - start_time).total_seconds()
        
        # Calculate total cost
        summary_cost = None
        # for chunk_cost in cost_list:
        #     if summary_cost is None:
        #         summary_cost = chunk_cost
        #         summary_cost.run_time = elapsed_time
        #     else:
        #         summary_cost.input_tokens += chunk_cost.input_tokens
        #         summary_cost.output_tokens += chunk_cost.output_tokens
        #         summary_cost.total_cost += chunk_cost.total_cost
                
        return "\n".join(summary_parts), summary_cost
    except Exception as e:
        raise Exception("get_description_concurrent --> " + str(e))

async def create_vectorstore(file_path):
    try:
        # file_title, file_ext = os.path.splitext(file_name)
        # ext_folder = file_ext.lstrip(".").upper()
        # file_folder_path = os.path.join(os.getenv("FOLDER_PREFIX"), username, ext_folder, file_title)
        # file_path = os.path.join(file_folder_path, file_name)
        # vectorstore_path = os.path.join(file_folder_path, "vectorstore")
        documents_list, markdown_data_text = await llama_parse_file(file_path)
        document_chunks = await get_chunks(markdown_data_text, 65536, 1024)
        embeddings=OpenAIEmbeddings(model=EMBEDDINGS_MODEL_NAME)
        start_time = datetime.now()
        print(f"{start_time.strftime('%Y-%m-%d %H:%M:%S:%MS')} Starting to create vectorstore")
        vectorstore = FAISS.from_documents(documents_list, embeddings)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        # vectorstore_creation_cost = await calculate_openai_embedding_cost([d.page_content for d in documents_list], elapsed_time)
        print(f"{end_time.strftime('%Y-%m-%d %H:%M:%S:%MS')} Vectorstore created in {(end_time - start_time).total_seconds()} seconds for file: {os.path.basename(file_path)} with {len(documents_list)} number of chunks")
        # os.makedirs(os.path.join(tmp_dir, vectorstore_path), exist_ok=True)
        # vectorstore_save_path = os.path.join(tmp_dir, vectorstore_path)
        # os.makedirs(vectorstore_save_path, exist_ok=True)
        # vectorstore.save_local(vectorstore_save_path)

        # await async_upload_folder(folder_s3_key=vectorstore_path, local_folder=vectorstore_save_path)

        description_future = asyncio.create_task(get_description_concurrent(document_chunks))
        # qa_pairs_future = asyncio.create_task(get_qa_pairs_concurrent(document_chunks, questions_number, language))
        
        # Wait for both to complete
        description, description_cost = await description_future
        # question_answer_pairs, qa_pairs_cost = await qa_pairs_future

        
        return vectorstore, description
    except Exception as e:
        raise Exception("create_vectorstore_and_qa_pairs --> " + str(e))

