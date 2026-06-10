"""CLI (D-cli) — invoked via `docker exec`.

`auth` is the recommended primary login path: interactive 2FA in a real TTY,
which sidesteps the unattended-2FA problem. The trusted session it writes to
/config is then reused (passwordless) by the API, worker, and scheduler (D2).

    docker exec -it icloud-sync-backend python -m app.cli auth
    docker exec -it icloud-sync-backend python -m app.cli sync --album Holidays
    docker exec -it icloud-sync-backend python -m app.cli status
"""
from __future__ import annotations

import typer

from app.services.icloud import get_icloud_service

app = typer.Typer(help="iCloud → NAS sync CLI", no_args_is_help=True)

DEFAULT_TEMPLATE = ["{year}", "{month}", "{album}"]


@app.command()
def auth() -> None:
    """Interactively authenticate (Apple ID, password, then 2FA)."""
    svc = get_icloud_service()
    apple_id = typer.prompt("Apple ID")
    password = typer.prompt("Password", hide_input=True)
    requires_2fa = svc.authenticate(apple_id, password)
    if requires_2fa:
        code = typer.prompt("2FA code")
        if not svc.submit_2fa(code):
            typer.secho("2FA validation failed.", fg=typer.colors.RED)
            raise typer.Exit(1)
    status_ = svc.get_status()
    typer.secho(
        f"Authenticated: {status_['authenticated']}  (needs_2fa: {status_['needs_2fa']})",
        fg=typer.colors.GREEN if status_["authenticated"] else typer.colors.RED,
    )


@app.command()
def sync(album: list[str] = typer.Option(None, "--album", "-a", help="album(s) to sync")) -> None:
    """Trigger a sync (from --album, or the saved schedule config)."""
    try:
        job_id = create_sync_job(album or None)
    except Exception as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(f"Enqueued download job {job_id}", fg=typer.colors.GREEN)


@app.command()
def status() -> None:
    """Print the latest job status and the schedule."""
    info = latest_status()
    job = info.get("last_job")
    if job:
        typer.echo(
            f"Last job #{job['id']}: {job['status']}  "
            f"(downloaded {job['downloaded']}, skipped {job['skipped']}, "
            f"failed {job['failed']}, total {job['total']})"
        )
    else:
        typer.echo("No jobs yet.")
    sched = info.get("schedule")
    if sched:
        state = "enabled" if sched["enabled"] else "disabled"
        typer.echo(f"Schedule ({state}): {sched['cron']}  next={sched['next_run']}  last={sched['last_run']}")
    else:
        typer.echo("No schedule configured.")


# --------------------------------------------------------------- helpers (DB)
def create_sync_job(albums: list[str] | None) -> int:
    from app.core.database import sync_session
    from app.models.assets import DownloadJob, Schedule
    from app.services.scheduler import CONFIG_KEYS
    from app.workers.tasks import run_download_job

    with sync_session() as s:
        if albums:
            spec = {"selected_albums": list(albums), "folder_structure": DEFAULT_TEMPLATE}
        else:
            sched = s.query(Schedule).order_by(Schedule.id).first()
            if sched is None or not sched.job_config:
                raise RuntimeError("No --album given and no saved schedule config to sync.")
            spec = {k: sched.job_config.get(k) for k in CONFIG_KEYS if sched.job_config.get(k) is not None}
        job = DownloadJob(status="pending", **spec)
        s.add(job)
        s.flush()
        job_id = job.id
        job.celery_task_id = run_download_job.delay(job_id).id
    return job_id


def latest_status() -> dict:
    from app.core.database import sync_session
    from app.models.assets import DownloadJob, Schedule

    with sync_session() as s:
        job = s.query(DownloadJob).order_by(DownloadJob.id.desc()).first()
        sched = s.query(Schedule).order_by(Schedule.id).first()
        return {
            "last_job": None if job is None else {
                "id": job.id, "status": job.status, "downloaded": job.downloaded_count,
                "skipped": job.skipped_count, "failed": job.failed_count, "total": job.total_assets,
            },
            "schedule": None if sched is None else {
                "enabled": sched.enabled, "cron": sched.cron_expression,
                "last_run": str(sched.last_run_at), "next_run": str(sched.next_run_at),
            },
        }


if __name__ == "__main__":
    app()
