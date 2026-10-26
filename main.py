from fastapi import FastAPI

app = FastAPI(
    title="ecommerce-nl2sql-agent",
    description="An ecommerce NL2SQL agent built from scratch",
    version="0.1.0",
    redoc_url=None,
)

@app.get("/health")
def health_check() -> dict[str,str]:
    return {
        "status": "OK",
        "service": "ecommerce-nl2sql-agent",
    }