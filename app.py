"""Start the local desktop dashboard (requires a graphical desktop)."""
import logging
import core


def main():
    from desktop import Application
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(core.DATA / "collector.log", encoding="utf-8"),
        ],
    )
    Application().mainloop()


if __name__ == "__main__":
    main()
