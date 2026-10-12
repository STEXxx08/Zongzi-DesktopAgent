from pydantic import BaseModel, Field


class WebSearchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class WebSearchResultData(BaseModel):
    title: str
    url: str
    snippet: str = ""
    engine: str | None = None
    score: float | None = None


class WebSearchPrediction(BaseModel):
    query: str
    results: list[WebSearchResultData] = Field(default_factory=list)
    error: str | None = None
    cached: bool = False


class WeatherQuery(BaseModel):
    location: str = Field(default="", max_length=200)


class WeatherCurrentData(BaseModel):
    time: str = ""
    temperature: float | None = None
    apparent_temperature: float | None = None
    relative_humidity: float | None = None
    precipitation: float | None = None
    weather_code: int | None = None
    wind_speed: float | None = None


class WeatherDailyData(BaseModel):
    date: str
    weather_code: int | None = None
    temperature_max: float | None = None
    temperature_min: float | None = None
    precipitation_probability_max: float | None = None


class WeatherPrediction(BaseModel):
    query: str
    location: str = ""
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = ""
    current: WeatherCurrentData | None = None
    daily: list[WeatherDailyData] = Field(default_factory=list)
    source: str = ""
    error: str | None = None
    cached: bool = False


class NewsQuery(BaseModel):
    query: str = Field(default="", max_length=300)


class NewsItemData(BaseModel):
    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    source: str = ""


class NewsPrediction(BaseModel):
    query: str
    items: list[NewsItemData] = Field(default_factory=list)
    error: str | None = None
    cached: bool = False


class SteamNewsQuery(BaseModel):
    game: str = Field(min_length=1, max_length=200)


class SteamNewsItemData(BaseModel):
    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    author: str = ""


class SteamNewsPrediction(BaseModel):
    query: str
    appid: int | None = None
    game_name: str = ""
    items: list[SteamNewsItemData] = Field(default_factory=list)
    error: str | None = None
    cached: bool = False


class GitHubReleaseQuery(BaseModel):
    repository: str = Field(min_length=1, max_length=300)


class GitHubReleaseItemData(BaseModel):
    name: str
    tag_name: str = ""
    url: str
    published_at: str = ""
    prerelease: bool = False
    summary: str = ""


class GitHubReleasePrediction(BaseModel):
    query: str
    repository: str = ""
    description: str = ""
    items: list[GitHubReleaseItemData] = Field(default_factory=list)
    error: str | None = None
    cached: bool = False


class ExchangeRateQuery(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class ExchangeRatePrediction(BaseModel):
    query: str
    amount: float | None = None
    base: str = ""
    quote: str = ""
    rate: float | None = None
    converted_amount: float | None = None
    date: str = ""
    source: str = ""
    error: str | None = None
    cached: bool = False