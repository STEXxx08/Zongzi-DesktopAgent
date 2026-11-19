import os
from typing import Any, Dict, Generator, Mapping, TypeVar

import aiohttp
import urllib3.util
from aiohttp import ClientResponse
from typeguard import typechecked
from condata.pipeline.abs_data import AbsractImageModelQuery

T = TypeVar("T")


@typechecked
def get_base_url(url: str) -> str:
    uri = urllib3.util.parse_url(url)
    base_url = f"{uri.scheme}://{uri.host}:{uri.port}"
    return base_url


class BaseAsyncPipeline:

    def __init__(self, base_url: str):
        self._base_url = base_url
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self):
        if self._session is not None:
            return self._session
        self._session = aiohttp.ClientSession(self._base_url)
        return self._session

    async def _post_multipart_json(
        self,
        endpoint: str,
        data: Mapping[str, Any],
        prediction_cls: type[T],
    ) -> T:
        try:
            async with self.session.post(endpoint, data=data) as resp:
                return await resp.json(encoding='utf8', loads=prediction_cls.model_validate_json)
        finally:
            for value in data.values():
                if hasattr(value, "close"):
                    try:
                        value.close()
                    except Exception:
                        pass

    async def _post_image_prediction(
        self,
        endpoint: str,
        query: AbsractImageModelQuery,
        prediction_cls: type[T],
    ) -> T:
        data = _parse_imgcap_query(query)
        return await self._post_multipart_json(endpoint, data, prediction_cls)

    async def _dispose_client_session(self):
        if self._session is None:
            return
        await self._session.close()

    async def close(self):
        await self._dispose_client_session()


@typechecked
def _parse_imgcap_query(query: AbsractImageModelQuery) -> Dict[str, Any]:
    # If the `query.img_path` path exists on the local machine,
    # then read the image as a binary file and add it to the `request.files`
    if os.path.exists(query.img_path):
        query.img_path = os.path.abspath(query.img_path).replace('\\', '/')
        img = open(query.img_path, 'rb')
        data = {'image': img,
                'json': query.model_dump_json()}
        return data
    # If the `query.img_path` path does not exist on the local machine, it must exist on the remote host
    # Note: If the remote host does not have this file neither, raise 500 error!
    else:
        return query.model_dump()


@typechecked
async def stream_generator(response: ClientResponse, chunk_size: int = -1) -> Generator[bytes, None, None]:
    while True:
        chunk = await response.content.read(chunk_size)
        if not chunk:
            break
        yield chunk
