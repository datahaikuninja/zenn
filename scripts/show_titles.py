from typing import Union
import os
import re
import pathlib


def parse_title_line(line: str) -> Union[str, None]:
    if m := re.match(r"^title: (.*)$", line):
        return m.group(1)


def extract_title(file: Union[str, bytes, os.PathLike]) -> str:
    with open(file) as f:
        while line := f.readline().strip():
            if title := parse_title_line(line):
                return title


def main():
    article_dir = pathlib.Path("./articles")
    for file in article_dir.iterdir():
        if m := re.search(r"(.*)\.md", file.name):
            slug = m.group(1)
            title = extract_title(file)
            print(f"{slug} -> {title}")


if __name__ == "__main__":
    main()
