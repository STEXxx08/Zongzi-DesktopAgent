from flask import Flask, request, Response, stream_with_context
from loguru import logger
from condata.pipeline.mllm import MLLMQuery, MLLMPrediction
from common.abs_app import AbstractApplication
from common.abs_model import AbstractModel

class MLLMApplication(AbstractApplication):
    def __init__(self, model: AbstractModel, host: str, port: int):
        super().__init__(model, "mllm")
        self.host = host
        self.port = port
        self._app = Flask(__name__)

    def run(self):
        self.model.load_model()
        self._init_routes()
        self._app.run(self.host, self.port, debug=False)

    def _init_routes(self):
        @self._app.route("/mllm/predict", methods=["POST"])
        def handle_predict():
            query = self._to_pipeline_format()
            prediction: MLLMPrediction = self.model.predict(query)
            logger.info(f"MLLM response: {prediction.response[:100]}...")
            return Response(
                response=prediction.model_dump_json(),
                status=200,
                mimetype='application/json'
            )

        @self._app.route("/mllm/stream-predict", methods=["POST"])
        def handle_stream_predict():
            query = self._to_pipeline_format()
            def generate():
                for pred in self.model.stream_predict(query):
                    yield pred.model_dump_json() + '\n'
            return Response(
                stream_with_context(generate()),
                mimetype='application/json'
            )

    def _to_pipeline_format(self) -> MLLMQuery:
        json_val = request.get_json()
        query = MLLMQuery.model_validate(json_val)
        for key in ("enable_thinking", "thinking_budget"):
            if key in json_val:
                object.__setattr__(query, key, json_val[key])
        return query