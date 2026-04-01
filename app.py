from src.app_logging import get_logger, install_global_exception_hooks, setup_logging
from src.branding import APP_NAME_EN, APP_NAME_ZH


def main() -> None:
    setup_logging()
    logger = get_logger(__name__)
    install_global_exception_hooks(logger)
    logger.info("Starting %s (%s)", APP_NAME_ZH, APP_NAME_EN)

    try:
        from src.ui_webview.window import WordPackWebviewApp

        app = WordPackWebviewApp()
        app.run()
    except Exception:
        logger.exception("Application exited with fatal error")
        raise


if __name__ == "__main__":
    main()
