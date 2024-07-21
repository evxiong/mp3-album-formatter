#!/usr/bin/env python3

import argparse
import os
import questionary
import re
import requests
import shutil
from mutagen.id3 import ID3, TALB, TPE2, APIC, TCON, TYER, TIT2, TPE1, TRCK, TPOS
from playwright.sync_api import sync_playwright
from rapidfuzz import process, fuzz
from tabulate import tabulate
from typing import List
from zipfile import ZipFile

# album metadata dict:
# {
#   album_name:str,
#   album_artists:[str],
#   cover:str,
#   genre:str,
#   year:str,
#   tracks:[{
#     name:str,
#     num:int,
#     total_num:int,
#     disc:int,
#     total_disc:int,
#     artists:[str]
#   }]
# }


class MismatchException(Exception):
    pass


def scrape(link: str) -> dict:
    """Scrapes Apple Music web player for album info."""

    print("Scraping metadata...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(link)
        album_name = page.locator(".headings__title")
        album_artists = page.locator(".headings__subtitles")
        cover = page.locator("picture > source").nth(1)
        genre_year = page.locator(".headings__metadata-bottom")
        discs = page.locator(".songs-list")

        # wait on all elements to be visible
        album_name.wait_for()
        album_artists.wait_for()
        genre_year.wait_for()
        discs.last.wait_for()

        # organize album metadata
        album = {
            "album_name": album_name.inner_text(),
            "album_artists": album_artists.all_inner_texts(),
            "cover": cover.get_attribute("srcset")
            .split()[0]
            .replace("296x296bb", "512x512bb"),
            "genre": genre_year.inner_text().split("\u2004·\u2004")[0].title(),
            "year": genre_year.inner_text().split("\u2004·\u2004")[1],
            "tracks": [
                {
                    "name": (
                        discs.nth(i)
                        .locator(".songs-list-row__song-name")
                        .nth(j)
                        .inner_text()
                    ),
                    "num": j + 1,
                    "total_num": discs.nth(i)
                    .locator(".songs-list-row__song-name-wrapper")
                    .count(),
                    "disc": i + 1,
                    "total_disc": discs.count(),
                    "artists": (
                        discs.nth(i)
                        .locator(".songs-list-row__song-name-wrapper")
                        .nth(j)
                        .locator(".songs-list-row__by-line")
                        .all_inner_texts()
                    ),
                }
                for i in range(discs.count())
                for j in range(
                    discs.nth(i).locator(".songs-list-row__song-name-wrapper").count()
                )
            ],
        }

        browser.close()
        return album


def match(file_names_ext: List[str], metadata: dict) -> List[str]:
    """Use RapidFuzz to match current songs with scraped track names

    Prompts for user input if matches can't be resolved

    args:
    - file_names: file names in specified directory, incl extensions
    - metadata: album metadata scraped from Apple Music

    returns:
    - List[str]: file names in track listing order

    """
    print("Matching songs...")
    file_names = [
        os.path.splitext(file)[0] for file in file_names_ext if file.endswith(".mp3")
    ]
    scraped_names = [track["name"] for track in metadata["tracks"]]

    if len(file_names) != len(scraped_names):
        raise MismatchException(
            f"The number of MP3 files in your directory ({len(
                file_names)}) is not equal to the number of songs in the album ({len(scraped_names)})"
        )

    # Normalized indel distance of shorter string's optimal alignment in longer string
    # each row is a file name; each col is a scraped name
    # matrix values are scores btwn 0 and 100
    # see: https://rapidfuzz.github.io/RapidFuzz/Usage/process.html#cdist
    matrix = process.cdist(file_names, scraped_names, scorer=fuzz.partial_ratio)

    # Get the index of the max score in each row,
    # corresponding to the most similar scraped name
    # matches[i] is most similar scraped_names index for index i in file_names
    matches = matrix.argmax(axis=1)
    matches_scores = matrix.max(axis=1)

    # If there are duplicates, they need to be manually corrected in cmd line
    matched_track_names = []  # returned list
    unmatched_tracks = []  # unresolved track names
    unresolved_files = []  # unresolved file names
    unmatched_inds = []  # indices of unresolved track names

    x = [[] for _ in range(len(file_names))]
    for i, scraped_i in enumerate(matches):
        x[scraped_i].append(i)
    for scraped_i, file_inds in enumerate(x):
        # omitted track names - must be manually resolved
        if len(file_inds) == 0:
            unmatched_inds.append(scraped_i)
            unmatched_tracks.append(scraped_names[scraped_i])
            matched_track_names.append(
                [
                    metadata["tracks"][scraped_i]["disc"],
                    metadata["tracks"][scraped_i]["num"],
                    scraped_names[scraped_i],
                    "*** UNMATCHED ***",
                    float("nan"),
                ]
            )
        # track names used more than once - must be manually resolved
        elif len(file_inds) > 1:
            unmatched_inds.append(scraped_i)
            unmatched_tracks.append(scraped_names[scraped_i])
            unresolved_files += [file_names_ext[i] for i in file_inds]
            matched_track_names.append(
                [
                    metadata["tracks"][scraped_i]["disc"],
                    metadata["tracks"][scraped_i]["num"],
                    scraped_names[scraped_i],
                    "*** UNMATCHED ***",
                    float("nan"),
                ]
            )
        # 1-to-1 matches - automatic
        else:
            matched_track_names.append(
                [
                    metadata["tracks"][scraped_i]["disc"],
                    metadata["tracks"][scraped_i]["num"],
                    scraped_names[scraped_i],
                    file_names_ext[file_inds[0]],
                    matches_scores[file_inds[0]],
                ]
            )

    matched_track_names.sort()

    print()
    questionary.print(f"{metadata["album_name"]}", style="bold underline")
    questionary.print(
        f"Auto-matched {len(matched_track_names) - len(unmatched_tracks)
                        } out of {len(matched_track_names)} tracks",
        style="bold",
        end="\n\n",
    )
    print(
        tabulate(
            matched_track_names,
            ["cd", "#", "track name", "matched file", "similarity"],
            floatfmt=".1f",
        )
    )

    if unresolved_files:
        print()
        questionary.print(
            "The following tracks could not be auto-matched:\n",
            style="bold",
        )
        print(
            tabulate(
                zip(unmatched_tracks, unresolved_files),
                ["Unmatched tracks", "Unmatched files"],
            ),
            end="\n\n\n",
        )

        questionary.print(
            "Select the corresponding file for each of the following tracks:\n",
            style="bold fg:ansibrightgreen",
        )

        for i in range(len(unmatched_tracks)):
            chosen = questionary.select(
                f"{unmatched_tracks[i]}",
                choices=unresolved_files,
                qmark="[>]",
            ).ask()
            matched_track_names[unmatched_inds[i]][3] = chosen
            unresolved_files.remove(chosen)

        print()
        print(
            tabulate(
                matched_track_names,
                ["cd", "#", "track name", "matched file", "similarity"],
                floatfmt=".1f",
            ),
            end="\n\n",
        )

    return [field[3] for field in matched_track_names]


def update(path: str, metadata: dict, keep_album: bool, keep_songs: bool) -> None:
    """Update current songs' metadata"""

    sorted_file_names = match(os.listdir(path), metadata)

    print()
    proceed = questionary.confirm(
        "Proceed with updating files?", qmark="[>]", default=True, auto_enter=False
    ).ask()

    if not proceed:
        exit(1)

    print("Updating metadata (this may take some time)...")

    for i, file_name in enumerate(sorted_file_names):
        audio = ID3(os.path.join(path, file_name), v2_version=3)

        #  set album name - TALB
        audio.setall("TALB", [TALB(encoding=3, text=[metadata["album_name"]])])

        #  set album artist(s) - TPE2
        audio.setall(
            "TPE2", [TPE2(encoding=3, text=[", ".join(metadata["album_artists"])])]
        )

        #  set album cover - APIC
        res = requests.get(metadata["cover"])
        audio.setall(
            "APIC",
            [
                APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=res.content,
                )
            ],
        )

        #  set album genre - TCON
        audio.setall("TCON", [TCON(encoding=3, text=[metadata["genre"]])])

        #  set album year - TYER
        audio.setall("TYER", [TYER(encoding=3, text=[metadata["year"]])])

        #  set track name - TIT2
        audio.setall("TIT2", [TIT2(encoding=3, text=[metadata["tracks"][i]["name"]])])

        #  set track artist(s) - TPE1
        audio.setall(
            "TPE1",
            [TPE1(encoding=3, text=[", ".join(metadata["tracks"][i]["artists"])])],
        )

        #  set track num - TRCK
        audio.setall(
            "TRCK",
            [
                TRCK(
                    encoding=3,
                    text=[
                        f"{metadata["tracks"][i]["num"]
                           }/{metadata["tracks"][i]["total_num"]}"
                    ],
                )
            ],
        )

        #  set track disc num - TPOS
        audio.setall(
            "TPOS",
            [
                TPOS(
                    encoding=3,
                    text=[
                        f"{metadata["tracks"][i]["disc"]
                           }/{metadata["tracks"][i]["total_disc"]}"
                    ],
                )
            ],
        )

        # save metadata
        audio.save(v2_version=3)

        # rename track file
        if not keep_songs:
            new_file_name = (
                " ".join(
                    re.sub(
                        r'[<>:"\/\\\|\?\*]', "", metadata["tracks"][i]["name"]
                    ).split()
                )
                + ".mp3"
            )
            os.rename(os.path.join(path, file_name), os.path.join(path, new_file_name))

    # rename album folder
    if not keep_album:
        new_album_name = " ".join(
            re.sub(r'[<>:"\/\\\|\?\*]', "", metadata["album_name"]).split()
        )
        os.rename(
            path, os.path.join(os.path.dirname(os.path.normpath(path)), new_album_name)
        )

    print("✨ Done! ✨")
    print(f"Successfully updated {len(sorted_file_names)} songs.")


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Updates album and song metadata",
        epilog="examples:\n  python3 formatter.py ./album-folder https://music.apple.com/us/album/thriller/269572838\n  python3 formatter.py -x ./zipped-album.zip ./album-dest-folder https://music.apple.com/us/album/thriller/269572838",
    )
    parser.add_argument(
        "album_path", help="relative path to album folder containing songs, or zip"
    )
    parser.add_argument(
        "dest_path",
        nargs="?",
        default=None,
        help="relative path to unzipped destination folder, required if using -x\ncreates folder if it does not exist",
    )
    parser.add_argument(
        "AM_album_link",
        help="Apple Music Web Player link to album\nex. https://music.apple.com/us/album/thriller/269572838",
    )
    parser.add_argument(
        "-x",
        "--extract",
        action="store_true",
        help="extract files from album_path zip file to dest_path",
    )
    parser.add_argument(
        "-a",
        "--keep-album",
        action="store_true",
        help="do not change album folder name\nif -x, do not change dest_path folder name",
    )
    parser.add_argument(
        "-s", "--keep-songs", action="store_true", help="do not change song file names"
    )

    args = parser.parse_args()

    # check for well-formed extract syntax
    if args.extract and args.dest_path is None:
        parser.error("-x requires dest_path (path to unzipped folder)")
    if not args.extract and args.dest_path:
        parser.error("dest_path requires -x flag")

    try:
        if args.extract:
            print("Unzipping file...")
            with ZipFile(args.album_path, "r") as z:
                z.extractall(args.dest_path)

        album_metadata = scrape(args.AM_album_link)
        update(
            args.dest_path if args.extract else args.album_path,
            album_metadata,
            args.keep_album,
            args.keep_songs,
        )

        if args.extract:
            print()
            proceed = questionary.confirm(
                f"Delete original ZIP file: {args.album_path}?",
                qmark="[>]",
                default=True,
                auto_enter=False,
            ).ask()
            if proceed and os.path.isfile(args.album_path):
                os.remove(args.album_path)
    except Exception as e:
        if os.path.isdir(args.dest_path):
            shutil.rmtree(args.dest_path)
        print(e)


if __name__ == "__main__":
    main()
