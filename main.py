from fastapi import FastAPI, UploadFile, HTTPException
from pydantic import BaseModel
import subprocess
import uuid
import os
import json

from pageindex.utils import (
    ChatGPT_API_async,
    extract_json,
    get_page_tokens,
    get_text_of_pdf_pages,
)

app = FastAPI()

UPLOAD_DIR = "uploaded_docs"

os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_MODEL = "gpt-oss:20b-cloud"


class AnswerRequest(BaseModel):
    name: str
    question: str
    model: str = DEFAULT_MODEL

class ProcessRequest(BaseModel):
    path: str
    model: str = DEFAULT_MODEL

def flatten_tree(nodes):
    """Recursively collect all nodes into a flat dict keyed by node_id."""
    result = {}
    for node in nodes:
        result[node["node_id"]] = node
        if "nodes" in node and node["nodes"]:
            result.update(flatten_tree(node["nodes"]))
    return result

def tree_for_prompt(nodes):
    """Strip text and summaries, keep only title/node_id/children structure."""
    lightweight = []
    for node in nodes:
        entry = {
            "node_id": node.get("node_id"),
            "title": node.get("title"),
        }
        if "nodes" in node and node["nodes"]:
            entry["nodes"] = tree_for_prompt(node["nodes"])
        lightweight.append(entry)
    return lightweight


async def query_pageindex(index_file, question, pdf_path, model):
    # Step 1: Load index and select relevant nodes via LLM
    with open(index_file, "r") as f:
        index_data = json.load(f)

    structure = index_data.get("structure", index_data)
    if not isinstance(structure, list):
        structure = [structure]

    lightweight_tree = tree_for_prompt(structure)

    selection_prompt = f"""You are given a document's hierarchical table of contents as JSON. Each node has a node_id, title, and optional summary.

A user is asking the following question:
"{question}"

Your task: identify which sections (by node_id) are most likely to contain the answer. Return between 1 and 5 node_ids.

Respond with JSON only in this exact format:
{{"thinking": "your brief reasoning", "node_list": ["0001", "0003"]}}

Here is the document structure:
{json.dumps(lightweight_tree, indent=2)}"""

    selection_response = await ChatGPT_API_async(model, selection_prompt)
    selection = extract_json(selection_response)
    node_ids = selection.get("node_list", [])

    if not node_ids:
        return {"answer": "Could not identify relevant sections for this question.", "sources": []}

    # Step 2: Extract text from selected nodes
    flat_nodes = flatten_tree(structure)
    pdf_pages = get_page_tokens(pdf_path)

    context_parts = []
    sources = []
    for nid in node_ids:
        node = flat_nodes.get(nid)
        if not node:
            continue
        start = node.get("start_index")
        end = node.get("end_index")
        if start is None or end is None:
            continue
        text = get_text_of_pdf_pages(pdf_pages, start, end)
        context_parts.append(f"## {node.get('title', 'Section')} (pages {start}-{end})\n{text}")
        sources.append({"node_id": nid, "title": node.get("title", ""), "start_page": start, "end_page": end})

    if not context_parts:
        return {"answer": "Could not extract text for the selected sections.", "sources": []}

    context_text = "\n\n".join(context_parts)

    # Step 3: Answer the question using extracted context
    answer_prompt = f"""Use the following document excerpts to answer the user's question. If the answer is not found in the provided text, say so.

Document excerpts:
{context_text}

Question: {question}

Answer:"""

    answer = await ChatGPT_API_async(model, answer_prompt)
    return {"answer": answer, "sources": sources}


@app.post("/upload")
async def upload_document(file: UploadFile):
    # Save uploaded file
    file_id = uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    with open(path, "wb") as f:
        f.write(await file.read())

    # Run PageIndex on this file
    result = subprocess.run(
        ["python3", "run_pageindex.py", "--pdf_path", path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr.strip())

    return {"id": file_id, "status": "indexed"}

@app.post("/process")
async def process(req: ProcessRequest):
    path = req.path
    model = req.model

    file_id = uuid.uuid4().hex

    # Run PageIndex on this file
    result = subprocess.run(
        ["python3", "run_pageindex.py", "--pdf_path", path, "--model", model],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr.strip())

    return {"id": file_id, "status": "indexed"}

@app.post("/answer")
async def answer_question(req: AnswerRequest):
    name = req.name
    question = req.question
    model = req.model
    # load index
#    index_file = f"indexes/{ID}.json"

    # derive pdf_path
    if name.find("/") != -1 :
        pdf_path = name  # .rsplit('.', 1)[0]
        index_file = f"results/{name.rsplit('.', 1)[0].rsplit('/', 1)[-1]}_structure.json"
    else:
        pdf_path = os.path.join(UPLOAD_DIR, f"{name}")
        index_file = f"results/{name}_structure.json"

    # perform reasoning retrieval
    try:
        answer = await query_pageindex(index_file, question, pdf_path, model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    result = answer
    return {"question": question, "answer": result["answer"], "sources": result["sources"]}
