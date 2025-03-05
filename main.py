# from rerank import compression_retriever
import anthropic
import pickle
import uvicorn
import time
from fastapi import FastAPI
from pydantic import BaseModel
from constants import model_name
from vectorstore_functions import create_vectorstore
import nest_asyncio
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_core.callbacks.manager import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document
from langchain_core.pydantic_v1 import Extra, root_validator
from langchain_core.utils import get_from_dict_or_env
from typing import Any, Dict, List, Optional, Sequence, Union
from langchain.retrievers import ContextualCompressionRetriever
from copy import deepcopy
import cohere
import os
from dotenv import load_dotenv
load_dotenv()
from concurrent.futures import ThreadPoolExecutor
from functools import partial
nest_asyncio.apply()

# app = FastAPI()

# class Input(BaseModel):
#     file_s3_url: str

qa_pairs = {}
with open('qa_pairs5.pkl', 'rb') as file:
    qa_pairs = pickle.load(file)

questions_list = []
for item in qa_pairs.keys():
    questions_list.append(item)

# print(questions_list)

cohere_api = os.getenv("COHERE_API_KEY")
embeddings=OpenAIEmbeddings(model="text-embedding-3-small")

# cohere_api="nOYMEU3Hx5z7JB7rcKyqkpnzKUQglufMGDf2ilzX"

class CohereRerank(BaseDocumentCompressor):
    """Document compressor that uses `Cohere Rerank API`."""

    client: Any = None
    """Cohere client to use for compressing documents."""
    top_n: Optional[int] = 3
    """Number of documents to return."""
    model: str = "rerank-english-v2.0"
    """Model to use for reranking."""
    cohere_api_key: Optional[str] = None
    """Cohere API key. Must be specified directly or via environment variable
        COHERE_API_KEY."""
    user_agent: str = "langchain"
    """Identifier for the application making the request."""

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid
        arbitrary_types_allowed = True

    @root_validator(pre=True)
    def validate_environment(cls, values: Dict) -> Dict:
        """Validate that api key and python package exists in environment."""
        if not values.get("client"):
            cohere_api_key = get_from_dict_or_env(
                values, "cohere_api_key", "COHERE_API_KEY"
            )
            client_name = values.get("user_agent", "langchain")
            values["client"] = cohere.Client(cohere_api_key, client_name=client_name)
        return values

    def rerank(
        self,
        documents: Sequence[Union[str, Document, dict]],
        query: str,
        *,
        model: Optional[str] = None,
        top_n: Optional[int] = -1,
        max_chunks_per_doc: Optional[int] = None,
    ) -> List[Dict[str, Any]]:

        if len(documents) == 0:  # to avoid empty api call
            return []
        docs = [
            doc.page_content if isinstance(doc, Document) else doc for doc in documents
        ]
        model = model or self.model
        top_n = top_n if (top_n is None or top_n > 0) else self.top_n
        results = self.client.rerank(
            query=query,
            documents=docs,
            model=model,
            top_n=top_n,
            max_chunks_per_doc=max_chunks_per_doc,
        )
        result_dicts = []
        for res in results.results:
            result_dicts.append(
                {"index": res.index, "relevance_score": res.relevance_score}
            )
        return result_dicts

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:

        compressed = []
        for res in self.rerank(documents, query):
            doc = documents[res["index"]]
            doc_copy = Document(page_content=doc.page_content, metadata=deepcopy(doc.metadata))
            doc_copy.metadata["relevance_score"] = res["relevance_score"]
            compressed.append(doc_copy)
        return compressed

PROMPT = """You will be given a list of questions in the
form of a python list.
You have to select and output only those questions which are relevant to the
document summary. These questions are from FIDIC redbook document.

Question list: '''{questions_list}'''

Your output should be in the form of a python list. Output only a list and nothing else.
Make sure list is closed andf all string literals are closed. If none of the questions are relevant to
the document summary simply output an empty list [].
"""

client = anthropic.Anthropic()

def get_documents(compression_retriever, query):
    documents = compression_retriever.invoke(query)
    return documents

def get_answer(compression_retriever, query):
    documents = get_documents(compression_retriever, query)
    messages = [{"role": "user", "content": query}]

    response = client.messages.create(
        model = model_name,
        max_tokens = 1024,
        system=f"""You are a helpful assistant that answers the question using the context
        provided to you. You answer only from the context provided to you and refuse 
        to answer questions which are out of scope. If the question says 'in the contract'
        or 'in the document' it means that it is talking about the context.
        Here is the context: '''{documents}'''
        Keep your answer concise and to the point. If the answer cannot be found from
        the context simply output "Answer not found from the document" and nothing else.
        """,
        messages=messages
    )
    ans = response.content[0].text
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = (input_tokens*(0.25/1000000))+ (output_tokens*(1.25/1000000))
    return ans, cost

def process_question(compression_retriever, question):
    answer, cost = get_answer(compression_retriever, question)
    return answer, cost

# @app.post("/query")
async def ask_ai(file_path):
    vectorstore, document_description = await create_vectorstore(file_path)
    retrieval = vectorstore.as_retriever(search_kwargs = {'k': 40})
    cohere_rerank = CohereRerank(cohere_api_key=cohere_api,model="rerank-multilingual-v3.0",top_n=3)
    compression_retriever = ContextualCompressionRetriever(
        base_compressor=cohere_rerank,
        base_retriever=retrieval
    )
    print(document_description)
    # document_description = request.description
    # print("total questions list")
    print(len(questions_list))
    shortlist_response = client.messages.create(
        model=model_name,
        max_tokens=3000,
        system=f"""You will be given a list of questions in the
        form of a python list.
        You have to select and output only those questions which are relevant to the
        document summary. These questions are from FIDIC redbook document.

        Question list: '''{questions_list}'''

        Your output should be in the form of a python list. Output only a list and nothing else.
        Output each question as it is. Do not change the question.
        Make sure list is closed and all string literals are closed. 
        Remember to output a list of maximum 50 strings. DO NOT output more than 50 strings.
        If none of the questions are relevant to the document summary simply output an empty list [].
        """,
        messages=[{"role": "user", "content": "Document description: " + document_description}]
    )

    total_cost = 0
    input_tokens = shortlist_response.usage.input_tokens
    output_tokens = shortlist_response.usage.output_tokens
    shortlist_cost = (input_tokens*(0.25/1000000))+ (output_tokens*(1.25/1000000))
    total_cost = total_cost + shortlist_cost

    if not shortlist_response or not shortlist_response.content:
        raise ValueError("Error: Received empty response from AI model.")


    shortlisted_questions = eval(shortlist_response.content[0].text.strip())
    # # shortlisted_questions = ['What are the insurance requirements specified in the document?']
    print("Shortlisted Questions:")
    print(shortlisted_questions)
    print(len(shortlisted_questions))
    if shortlisted_questions == []:
        return "No deviation found."

    start_time = time.time()
    answers = []
    # for question in shortlisted_questions:
    #     answer, cost = get_answer(compression_retriever, question)
    #     total_cost = total_cost + cost
    #     answers.append(answer)
    
    process_with_retriever = partial(process_question, compression_retriever)
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(process_with_retriever, shortlisted_questions))
    answers, costs = zip(*results)
    answers = list(answers)
    total_cost = sum(costs)
    print("answers:")
    # # print(answers)
    ans = ""
    count = 1
    def process_deviation(i):
        """Processes a single question-answer pair and returns the deviation response."""
        question = shortlisted_questions[i]

        try:
            correct_answer = qa_pairs[question]
        except KeyError:  # More specific exception handling
            print(f"Key error: {question}")
            return None

        answer = answers[i]
        deviation_response = client.messages.create(
            model=model_name,
            max_tokens=1024,
            system=f"""For a particular question from the FIDIC redbook document
            you have to analyze the answer from the data source and compare it with the 
            FIDIC document snippet as correct answer. If there are deviations of the data source answer from the correct
            answer you have to highlight the deviation. If the data source answer says answer not found
            or the answer is ambiguous simply output "No deviation found" and nothing else.
            Here is the FIDIC document snippet: '''{correct_answer}'''.
            You should analyze both data source answer and correct answer and attach key
            terms to it. You should attach these terms only when there is a deviation
            from correct answer and not otherwise. The terms should be attached as follows -
            1.) critical - If the data source answer is deviating from the correct answer in a very serious or risky case.
            2.) moderate - If there is a major deviation of the data source answer from the correct answer. A deviation in definition or in values.
            3.) minor - If there is a very small deviation of the data source answer from the correct answer.
            Add the term in the beginning of your answer.
            Remember, If the data source answer is similar to the correct answer simply output 
            "No deviation found" and nothing else. Also, if the data source answer says "not found" 
            or any terms is not found or mentioned you have to output "No deviation found" and nothing else.
            Do not use the word "answer" or words "correct answer" in your response. Just highlight the deviation in case there is.
            """,
            messages=[{"role": "user", "content": "Data source answer: " + answer}]
        )

        deviation_response_text = deviation_response.content[0].text
        # print(deviation_response_text)
        return deviation_response_text
    
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(process_deviation, range(len(answers))))

    # Filter out None values (in case of errors)
    deviation_responses = [res for res in results if res is not None]

    # for i in range(0, len(answers)):
    #     # try:
    #     question = shortlisted_questions[i]
    #     try:
    #         correct_answer = qa_pairs[question]
    #     except:
    #         print(question)
    #         print("Key error")
    #         continue
    #     answer = answers[i]
    #     deviation_response = client.messages.create(
    #         model=model_name,
    #         max_tokens=1024,
    #         system=f"""For a particular question from the FIDIC redbook document
    #         you have to analyze the answer from the data source and compare it with the 
    #         correct answer. If there are deviations of the data source answer from the correct
    #         answer you have to highlight the deviation. If the data source answer says answer not found
    #         or the answer is ambiguous simply output "No deviation found" and nothing else.
    #         Here is the FIDIC document snippet: '''{correct_answer}'''.
    #         If the data source answer is similar to the correct answer simply output 
    #         "No deviation found" and nothing else. Do not use the word "answer" or words "correct answer"
    #         in your response. Just highlight the deviation.
    #         """,
    #         messages=[{"role": "user", "content": "Data source answer: " + answer}]
    #     )

    #     print(deviation_response.content[0].text)
    #     deviation_response_text = deviation_response.content[0].text
    #     if not ("No deviation found" in deviation_response_text or "no deviation found" in deviation_response_text):
    #         ans = ans + '\n' + str(count) + ". " + deviation_response_text + '\n'
    #         count = count + 1
    #     input_tokens = deviation_response.usage.input_tokens
    #     output_tokens = deviation_response.usage.output_tokens
    #     new_cost = (input_tokens*(0.25/1000000))+ (output_tokens*(1.25/1000000))
    #     total_cost = total_cost + new_cost
    #     # except:
    #     #     continue
    # print(ans)
    for deviation_response_text in deviation_responses:
        if not ("No deviation found" in deviation_response_text or "no deviation found" in deviation_response_text):
            ans = ans + '\n' + str(count) + ". " + deviation_response_text + '\n'
            count = count + 1
    end_time = time.time()
    print("Total time taken:")
    print(end_time - start_time)
    return ans

# if __name__ == '__main__':
#     uvicorn.run(app, host="0.0.0.0", port=8000)