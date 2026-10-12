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
            try:
                query = WebSearchQuery.model_validate(request.get_json(silent=True) or {})
            except ValidationError as exc:
                return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400

            try:
                prediction = self.model.search(query.query)
            except Exception as exc:
                logger.exception(f"SearXNG request failed: {exc}")
                prediction = WebSearchPrediction(
                    query=query.query,
                    error=str(exc),
                )
            return Response(
                response=prediction.model_dump_json(),
                status=200,
                mimetype="application/json",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

        @self._app.route("/web-search/weather", methods=["POST"])
        def weather():
            try:
                query = WeatherQuery.model_validate(request.get_json(silent=True) or {})
            except ValidationError as exc:
                return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400

            logger.info(f"Weather request received: location={query.location!r}")

            if self.weather_model is None:
                prediction = WeatherPrediction(
                    query=query.location,
                    error="Weather module is disabled",
                )
            else:
                try:
                    prediction = self.weather_model.get_weather(query.location)
                except Exception as exc:
                    logger.exception(f"Weather request failed: {exc}")
                    prediction = WeatherPrediction(query=query.location, error=str(exc))
            return self._json_response(prediction)

        @self._app.route("/web-search/news", methods=["POST"])
        def news():
            try:
                query = NewsQuery.model_validate(request.get_json(silent=True) or {})
            except ValidationError as exc:
                return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400
            logger.info(f"News request received: query={query.query!r}")
            try:
                prediction = self.news_model.get_news(query.query) if self.news_model else NewsPrediction(
                    query=query.query, error="News module is disabled"
                )
            except Exception as exc:
                logger.exception(f"News request failed: {exc}")
                prediction = NewsPrediction(query=query.query, error=str(exc))
            return self._json_response(prediction)

        @self._app.route("/web-search/steam", methods=["POST"])
        def steam():
            try:
                query = SteamNewsQuery.model_validate(request.get_json(silent=True) or {})
            except ValidationError as exc:
                return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400
            logger.info(f"Steam request received: game={query.game!r}")
            try:
                prediction = self.steam_model.get_news(query.game) if self.steam_model else SteamNewsPrediction(
                    query=query.game, error="Steam module is disabled"
                )
            except Exception as exc:
                logger.exception(f"Steam request failed: {exc}")
                prediction = SteamNewsPrediction(query=query.game, error=str(exc))
            return self._json_response(prediction)

        @self._app.route("/web-search/github", methods=["POST"])
        def github():
            try:
                query = GitHubReleaseQuery.model_validate(request.get_json(silent=True) or {})
            except ValidationError as exc:
                return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400
            logger.info(f"GitHub request received: repository={query.repository!r}")
            try:
                prediction = self.github_model.get_releases(query.repository) if self.github_model else GitHubReleasePrediction(
                    query=query.repository, error="GitHub module is disabled"
                )
            except Exception as exc:
                logger.exception(f"GitHub request failed: {exc}")
                prediction = GitHubReleasePrediction(query=query.repository, error=str(exc))
            return self._json_response(prediction)

        @self._app.route("/web-search/exchange-rate", methods=["POST"])
        def exchange_rate():
            try:
                query = ExchangeRateQuery.model_validate(request.get_json(silent=True) or {})
            except ValidationError as exc:
                return jsonify({"error": "invalid_request", "detail": exc.errors()}), 400
            try:
                prediction = self.exchange_rate_model.convert(query.text) if self.exchange_rate_model else ExchangeRatePrediction(
                    query=query.text, error="Exchange-rate module is disabled"
                )
            except Exception as exc:
                logger.exception(f"Exchange-rate request failed: {exc}")
                prediction = ExchangeRatePrediction(query=query.text, error=str(exc))
            return self._json_response(prediction)

    @staticmethod
    def _json_response(prediction):
        return Response(
            response=prediction.model_dump_json(),
            status=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )