from pydantic import BaseModel


class VersionResponse(BaseModel):
    name: str
    version: str
    git_sha: str
    build_time: str
