from abc import abstractmethod

from loguru import logger
from websockets import ProtocolError
from condata.protocol.protocol import ZongziProtocol

from common.concurrent.abs_runnable import ThreadRunnable
from common.web.json_ws import JsonWsServer


##########################################
#  Zongzi Protocol Web Socket Server  #
#          Author: AkagawaTsurunaki      #
##########################################

# 命名说明：`protocol` 字段的默认值 "ZongziProtocol" 与其他字段共同构成**线上格式**，
# 改名会让按旧串通信的远端连不上。本仓库内没有任何代码 import 本模块或 json_ws.py
# （保留的上游能力，非在用路径），因此这里只做 Python 标识符层面的中性化命名。
class ZongziProtocolWsServer(ThreadRunnable):
    def __init__(self, host: str, port: int):
        super().__init__()
        self._protocol = "ZongziProtocol"
        self._jws = JsonWsServer(host=host, port=port, subprotocols=[self._protocol])
        self._version = "1.1"

    def name(self):
        return "ZongziProtocolWebsocket"

    def start(self):
        super().start()
        self._init()
        self._jws.start()

    def stop(self):
        super().stop()
        self._jws.stop()

    @property
    def is_connected(self):
        if self._jws.connections > 0:
            return True
        return False

    def send(self, action: str, data: any, message: str = "", code: int = 0):
        protocol = ZongziProtocol(message=message,
                                   code=code,
                                   action=action,
                                   data=data)
        self._jws.send_json(protocol)

    def _init(self):
        def on_json_msg(_, protocol: dict | list):
            protocol = self._validate_zongzi_protocol(protocol)
            logger.debug(f"Validated Zongzi Protocol {protocol.data}")
            self.on_protocol(protocol)

        self._jws.on_msg_handlers += [on_json_msg]

    def _validate_zongzi_protocol(self, data: dict | list):
        recv_obj = ZongziProtocol.model_validate(data)
        if recv_obj.protocol == self._protocol and recv_obj.version == self._version:
            return recv_obj
        raise ProtocolError("Invalid ZongziProtocol")

    @abstractmethod
    def on_protocol(self, protocol: ZongziProtocol):
        raise NotImplementedError()

    @abstractmethod
    def on_disconnect(self, ws_id: str):
        raise NotImplementedError()
