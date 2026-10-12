# Core Web Search

The `web_search` domain contains these tools:

| Tool | Data source | Needs SearXNG |
|---|---|---|
| `search` | SearXNG (`format=json`) through `/web-search/search` | yes |
| `news` | SearXNG news category through `/web-search/news` | yes |
| `weather` | Open-Meteo (geocoding + forecast) through `/web-search/weather` | no |
| `steam` | Steam news API through `/web-search/steam` | no |
| `github` | GitHub releases API through `/web-search/github` | no |
| `exchange_rate` | Frankfurter v2 through `/web-search/exchange-rate` | no |

Only `search` and `news` depend on SearXNG; the other four keep working when no
SearXNG instance is reachable. The Bot calls only these Core endpoints, so local
and remote Core deployments use the same protocol. If the structured weather
request fails, Bot can fall back to the normal search tool.

## Configure SearXNG

SearXNG is an independently installed meta search engine: it is not a pip
package and is not shipped with this repository. It may run on the same host, in
a WSL distribution, or on another Linux machine — `WebSearch.config.endpoint`
in the Core config has to point at wherever it actually listens.

Apply `searxng/settings.yml` to the existing SearXNG installation, or copy the
corresponding options into its current settings file. Start SearXNG using the
installation's normal command or service manager.

SearXNG's JSON output is enabled in `settings.yml`; without it, requests using
`format=json` return HTTP 403.

When SearXNG lives in a systemd unit inside WSL on the Windows host that also
runs this stack, `settings.optional_dependencies` in
`Core/agent-stack/registry.json` starts it best-effort on `agent-stack.cmd start`
(a failure only prints a warning and never aborts the stack); `stop` leaves it
running, and `status` lists it separately. See `Core/agent-stack/README.md`.

## Start the Core gateway

From the `core` directory:

```bash
python starter.py web_search --config config.yaml
```

For a remote Core deployment, change Bot `service.web_search.endpoint` to the
Core server address, and change `service.web_search.weather.endpoint` as well.
Set Core `WebSearch.weather.default_location` if commands such as `看天气` should
work without an explicit city. Do not expose SearXNG itself publicly unless
authentication, rate limiting, and a reverse proxy have been configured.