from fastapi import FastAPI

from domain.adapters import api as adapters
from domain.agent import api as agent
from domain.config import api as config
from domain.plugins import api as plugins
from domain.processors import api as processors
from domain.tasks import api as tasks
from domain.virtual_fs import api as virtual_fs
from domain.virtual_fs.mapping import s3_api, webdav_api
from domain.virtual_fs.search import search_api


def include_routers(app: FastAPI):
    app.include_router(agent.router)
    app.include_router(adapters.router)
    app.include_router(search_api.router)
    app.include_router(virtual_fs.router)
    app.include_router(config.router)
    app.include_router(processors.router)
    app.include_router(tasks.router)
    app.include_router(plugins.router)
    app.include_router(webdav_api.router)
    app.include_router(s3_api.router)
