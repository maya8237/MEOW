from meow.infrastructure.scheduling import _validate_run


def test_saved_schedule_accepts_legacy_unattended_flag(tmp_path):
    # Existing schedules persist argv rather than rebuilding it on update.
    _validate_run(
        tmp_path,
        [
            "run",
            "--unattended",
            "--work-dir",
            str(tmp_path),
            "--name",
            "saved-task",
            "saved task",
        ],
    )
