import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def summarize_agents():
    import os as _os
    from pathlib import Path

    from openai import OpenAI as _OpenAI

    # _data_sensitivity = _os.environ["AMBER_DATA_SENSITIVITY"]
    # _provider_zone = _os.environ["AMBER_PROVIDER_ZONE"]
    _data_sensitivity = 'synthetic'
    _provider_zone = 'local'

    _allowed_zones = {
        "phi": {"local", "institution"},
        "limited": {"local", "institution"},
        "deidentified": {"local", "institution", "external_baa"},
        "synthetic": {"local", "institution", "external_baa", "external"},
    }
    if _data_sensitivity not in _allowed_zones:
        raise ValueError(f"Unknown data sensitivity: {_data_sensitivity!r}")
    if _provider_zone not in _allowed_zones[_data_sensitivity]:
        raise PermissionError(
            f"Provider zone {_provider_zone!r} is not permitted for "
            f"data sensitivity {_data_sensitivity!r}"
        )

    summary_provenance = {
        "data_sensitivity": _data_sensitivity,
        "provider_zone": _provider_zone,
    }
    _agents_text = Path("AGENTS.md").read_text(encoding="utf-8")
    _azure_endpoint = _os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    _azure_base_url = (
        _azure_endpoint
        if _azure_endpoint.endswith("/openai/v1")
        else f"{_azure_endpoint}/openai/v1"
    )
    _response = _OpenAI(
        api_key=_os.environ["AZURE_OPENAI_API_KEY"],
        base_url=f"{_azure_base_url}/",
    ).responses.create(
        model="gpt-5.6-sol",
        instructions=(
            "Summarize these repository instructions for a coding agent. "
            "Prioritize non-negotiable rules, architecture boundaries, data safety, "
            "and required validation commands. Use concise Markdown."
        ),
        input=_agents_text,
    )
    agents_summary = _response.output_text
    return agents_summary, summary_provenance


@app.cell
def render_agents_summary(agents_summary, summary_provenance):
    import marimo as _mo

    _mo.vstack(
        [
            _mo.md(agents_summary),
            _mo.md(
                "**Provenance:** "
                f"sensitivity=`{summary_provenance['data_sensitivity']}`, "
                f"provider zone=`{summary_provenance['provider_zone']}`"
            ),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
