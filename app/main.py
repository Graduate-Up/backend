from fastapi import FastAPI

app = FastAPI(
    title="졸UP Backend API",
    description="경북대학교 컴퓨터학부 졸업요건 도우미",
    version="0.1.0"
)

@app.get("/")
def root():
    return {"message": "졸UP 백엔드 서버 실행 성공!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
