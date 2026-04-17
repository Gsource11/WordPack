from src.app_logging import get_logger, install_global_exception_hooks, setup_logging
from src.branding import APP_TITLE
from src.single_instance import SingleInstanceManager


def main() -> None:
    setup_logging()
    logger = get_logger(__name__)
    install_global_exception_hooks(logger)
    logger.info("Starting %s", APP_TITLE)
    instance = SingleInstanceManager(app_id="WordPack")

    try:
        is_primary = instance.acquire()
        if not is_primary:
            sent = instance.send_command("SHOW_MAIN")
            logger.info("Existing instance detected; forward SHOW_MAIN command sent=%s", bool(sent))
            return

        from src.ui_webview.window import WordPackWebviewApp

        app = WordPackWebviewApp()
        instance.set_command_handler(app.handle_external_command)
        app.run()
    except Exception:
        logger.exception("Application exited with fatal error")
        raise
    finally:
        instance.stop()


if __name__ == "__main__":
    main()
