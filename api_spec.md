POST /query
Content-Type: application/json
Body: {"question": "사용자 질의"}

Response:
{
  "answer": "근거 기반 응답",
  "contexts": [{"doc_id": "...", "text": "..."}, ...],
  "trace": [{"step": "retrieve", "input": "...", "output": "..."}, ...]
}