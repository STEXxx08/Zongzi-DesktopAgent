from flask import Flask, Response, jsonify, request
from loguru import logger
from pydantic import ValidationError

from common.abs_app import AbstractApplication
from condata.pipeline.web_search import (
    ExchangeRatePrediction,
    ExchangeRateQuery,
    GitHubReleasePrediction,
    GitHubReleaseQuery,
    NewsPrediction,
    NewsQuery,
    SteamNewsPrediction,
    SteamNewsQuery,
    WeatherPrediction,
    WeatherQuery,
    WebSearchPrediction,
    WebSearchQuery,
)


class WebSearchApplication(AbstractApplication):
    def __init__(
        self,
        model,
        host: str,
        port: int,
        weather_model=None,
        news_model=None,
        steam_model=None,
        github_model=None,
        exchange_rate_model=None,
    ):
        super().__init__(model, "web_search")
        self.host = host
        self.port = port
        self.weather_model = weather_model
        self.news_model = news_model
        self.steam_model = steam_model
        self.github_model = github_model
        self.exchange_rate_model = exchange_rate_model
        self._app = Flask(__name__)

    def run(self):
        self.init()
        self._app.run(self.host, self.port, False)

    def init(self):
        @self._app.route("/web-search/health", methods=["GET"])
        def health():
            return jsonify({"status": "ok"})

        @self._app.route("/web-search/search", methods=["POST"])
        def search():
            return self._dispatch_query(
                query_cls=WebSearchQuery,
                pred_cls=WebSearchPrediction,
                query_attr="query",
                model_attr="model",
                method_name="search",
                label="SearXNG",
            )

        @self._app.route("/web-search/weather", methods=["POST"])
        def weather():
            return self._dispatch_query(
                query_cls=WeatherQuery,
                pred_cls=WeatherPrediction,
                query_attr="location",
                model_attr="weather_model",
                method_name="get_weather",
                label="Weather",
                disabled_name="Weather",
                log_field="location",
            )

        @self._app.route("/web-search/news", methods=["POST"])
        def news():
            return self._dispatch_query(
                query_cls=NewsQuery,
                pred_cls=NewsPrediction,
                query_attr="query",
                model_attr="news_model",
                method_name="get_news",
                label="News",
                disabled_name="News",
                log_field="query",
            )

        @self._app.route("/web-search/steam", methods=["POST"])
        def steam():
            return self._dispatch_query(
                query_cls=SteamNewsQuery,
                pred_cls=SteamNewsPrediction,
                query_attr="game",
                model_attr="steam_model",
                method_name="get_news",
                label="Steam",
                disabled_name="Steam",
                log_field="game",
            )

        @self._app.route("/web-search/github", methods=["POST"])
        def github():
            return self._dispatch_query(
                query_cls=GitHubReleaseQuery,
                pred_cls=GitHubReleasePrediction,
                query_attr="repository",
                model_attr="github_model",
                method_name="get_releases",
                label="GitHub",
                disabled_name="GitHub",
                log_field="repository",
            )

        @self._app.route("/web-search/exchange-rate", methods=["POST"])
        def exchange_rate():
            return self._dispatch_query(
                query_cls=ExchangeRateQuery,
                pred_cls=ExchangeRatePrediction,
                query_attr="text",
                model_attr="exchange_rate_model",
                method_name="convert",
                label="Exchange-rate",
                disabled_name="Exchange-rate",
            )

    def _dispatch_query(
        self,
        *,
        query_cls,
        pred_cls,
        query_attr: str,
        model_attr: str,
        method_name: str,
        label: str,
        disabled_name: str | None = None,
        log_field: str | None = None,
    ):
        try:
            query = query_cls.model_validate(request.get_json(silent=True) or {})
        except ValidationError as exc:
            return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400

        query_value = getattr(query, query_attr)
        if log_field is not None:
            logger.info(f"{label} request received: {log_field}={query_value!r}")

        sub_model = getattr(self, model_attr)
        if sub_model is None and disabled_name is not None:
            prediction = pred_cls(query=query_value, error=f"{disabled_name} module is disabled")
        else:
            try:
                prediction = getattr(sub_model, method_name)(query_value)
            except Exception as exc:
                logger.exception(f"{label} request failed: {exc}")
                prediction = pred_cls(query=query_value, error=str(exc))
        return self._json_response(prediction)

    @staticmethod
    def _json_response(prediction):
        return Response(
            response=prediction.model_dump_json(),
            status=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )