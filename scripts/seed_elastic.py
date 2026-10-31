"""Create DBIC-owned lab samples only; never replace documents, mappings or existing indices."""

import json
import ssl
from pathlib import Path

import httpx

from dbic.adapters.http import validate_endpoint
from dbic.settings import Settings


def seed(client, mapping, samples):
    response = client.get("/db-events-lab/_mapping")
    if response.status_code == 404:
        response = client.put("/db-events-lab", json=mapping)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"lab index creation failed HTTP {response.status_code}; no overwrite attempted"
            )
    elif response.status_code != 200:
        raise RuntimeError(f"lab mapping read failed HTTP {response.status_code}")
    # Re-read after creation to guard a name collision or an unexpected mapping.
    response = client.get("/db-events-lab/_mapping")
    if response.status_code != 200:
        raise RuntimeError(f"lab mapping verification failed HTTP {response.status_code}")
    existing = response.json()
    if (
        set(existing) != {"db-events-lab"}
        or existing["db-events-lab"]["mappings"] != mapping["mappings"]
    ):
        raise RuntimeError("existing lab index is not the expected DBIC mapping; no changes made")
    for sample in samples:
        response = client.put(
            f"/db-events-lab/_create/{sample['id']}",
            params={"refresh": "wait_for"},
            json=sample["document"],
        )
        if response.status_code == 409:
            prior = client.get(f"/db-events-lab/_doc/{sample['id']}")
            if prior.status_code != 200 or prior.json().get("_source") != sample["document"]:
                raise RuntimeError("sample ID collision; existing document was not overwritten")
        elif response.status_code != 201:
            raise RuntimeError(f"sample creation failed HTTP {response.status_code}")


def main():
    root = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=root / ".env")
    url = validate_endpoint(settings.elastic_url)
    key = settings.elastic_api_key.get_secret_value() if settings.elastic_api_key else ""
    headers = {"Authorization": f"ApiKey {key}"} if key else {}
    with httpx.Client(
        base_url=url,
        timeout=10,
        trust_env=False,
        follow_redirects=False,
        verify=ssl.create_default_context(cafile=settings.history_ca_file or None),
        headers=headers,
    ) as client:
        seed(
            client,
            json.loads((root / "configs/elastic/db-events-lab.json").read_text()),
            json.loads((root / "configs/elastic/events.sample.json").read_text()),
        )
    print("PASS: two DBIC lab samples present; existing documents and mappings were not replaced")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f"FAIL: lab seed: {message}") from None
