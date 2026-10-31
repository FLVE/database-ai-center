import httpx

from dbic.adapters.http import ReadOnlyHTTP
from dbic.adapters.metrics.null import NullMetrics
from dbic.adapters.metrics.prometheus import PrometheusMetricsAdapter
from dbic.adapters.search.elastic import ElasticSearchAdapter
from dbic.adapters.search.null import NullSearch
from dbic.core.ports import EvidenceSearchPort, MetricsPort
from dbic.settings import Settings


def historical_adapters(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[MetricsPort, EvidenceSearchPort]:
    metrics: MetricsPort = NullMetrics()
    search: EvidenceSearchPort = NullSearch()
    if settings.metrics_backend == "prometheus":
        token = settings.prometheus_token.get_secret_value() if settings.prometheus_token else ""
        metrics = PrometheusMetricsAdapter(
            ReadOnlyHTTP(
                settings.prometheus_url,
                authorization=f"Bearer {token}" if token else None,
                ca_file=settings.history_ca_file or None,
                transport=transport,
            ),
            instance_label=settings.prometheus_instance_label,
            instance_value=settings.prometheus_instance_value,
        )
    if settings.search_backend == "elastic":
        token = settings.elastic_api_key.get_secret_value() if settings.elastic_api_key else ""
        search = ElasticSearchAdapter(
            ReadOnlyHTTP(
                settings.elastic_url,
                authorization=f"ApiKey {token}" if token else None,
                ca_file=settings.history_ca_file or None,
                transport=transport,
            )
        )
    return metrics, search
