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


class MismatchException(Exception):
    pass


class Formatter:
    def __init__(
        self,
        album_path: str,
        dest_path: str,
        album_link: str,
        extract: bool = False,
        preserve_album: bool = False,
        preserve_songs: bool = False,
    ):
        if os.path.exists(album_path):
            self.__album_path = album_path
        else:
            raise FileNotFoundError(f"Could not find {album_path}")
        self.__dest_path = dest_path
        self.__album_link = album_link
        self.__flag_extract = extract
        self.__flag_preserve_album = preserve_album
        self.__flag_preserve_songs = preserve_songs
        self.__metadata = {}

    def run(self):
        try:
            self.unzip()
            self.flatten()
            self.scrape()
            filenames_to_track_inds = self.match()
            self.update(filenames_to_track_inds)
            self.delete_zip()
        except Exception as e:
            # delete unzipped folder
            if self.__dest_path and os.path.isdir(self.__dest_path):
                shutil.rmtree(self.__dest_path)
            print(e)

    def unzip(self) -> None:
        """Unzip album"""
        if self.__flag_extract:
            print("Unzipping file...")
            with ZipFile(self.__album_path, "r") as z:
                z.extractall(self.__dest_path)

    def delete_zip(self) -> None:
        """Prompt user to delete original ZIP"""
        if self.__flag_extract:
            print()
            proceed = questionary.confirm(
                f"Delete original ZIP file: {self.__album_path}?",
                qmark="[>]",
                default=True,
                auto_enter=False,
            ).ask()
            if proceed and os.path.isfile(self.__album_path):
                os.remove(self.__album_path)
                print("File deleted.")

    def flatten(self) -> None:
        """Move all files out of nested folders into root album folder

        Deletes all nested folders after moving files
        """
        root_folder = self.__dest_path if self.__flag_extract else self.__album_path
        for root, _, files in os.walk(root_folder):
            for file in files:
                if root != root_folder:
                    # rename nested file if conflict
                    name, ext = os.path.splitext(file)
                    a = ""
                    while os.path.exists(os.path.join(root_folder, name + a + ext)):
                        a += "_"
                    new_filepath = os.path.join(root, name + a + ext)
                    os.rename(os.path.join(root, file), new_filepath)

                    # move nested file
                    shutil.move(new_filepath, root_folder)

        # delete all nested folders
        for subfolder in next(os.walk(root_folder))[1]:
            shutil.rmtree(os.path.join(root_folder, subfolder))

    def scrape(self) -> dict:
        """Scrapes Apple Music web player for album info.

        Updates self.__metadata with the following info:
        {
            album_name: str,
            album_artists: [str],
            cover: str,  # link to cover art
            genre: str,
            year: str,
            tracks:[{
                name: str,
                num: int,  # track num on this disc
                total_num: int,  # total num of tracks on this disc
                disc: int,  # current disc num
                total_disc: int,  # total num of discs
                artists: [str]  # track-specific artists
            }]
        }

        Returns self.__metadata
        """

        print("Scraping metadata...")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(self.__album_link)
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
            self.__metadata = {
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
                        discs.nth(i)
                        .locator(".songs-list-row__song-name-wrapper")
                        .count()
                    )
                ],
            }

            browser.close()
            return self.__metadata

    def __match_less(
        self,
        best_match_inds,
        file_names_ext: List[str],
        scraped_tracks_dict: dict[str, int],
        scraped_tracks: List[str],
    ) -> dict[str, int]:
        """Match files to tracks when there are less files than tracks in album.

        Args:
        - matrix: ndarray dim # files x # tracks
        - file_names_ext: List[str] list of file names with extensions (.mp3)

        Returns:
        - dict[str, int]: file name -> track index (0-based)

        """
        # prompt user input for confirmation
        proceed = questionary.confirm(
            f"There are less MP3 files ({len(file_names_ext)}) than songs in the album ({len(scraped_tracks)}). Proceed?",
            qmark="[>]",
            default=True,
            auto_enter=False,
        ).ask()
        if not proceed:
            exit(1)

        result = {}  # file names -> track index (0-based)

        # display album songs
        print()
        questionary.print(
            f"{self.__metadata["album_name"]}", style="bold underline", end=""
        )
        questionary.print(
            f" - {", ".join(self.__metadata["album_artists"])}\n", style="bold"
        )
        print(
            tabulate(
                [
                    (
                        track["disc"],
                        track["num"],
                        track["name"],
                        ", ".join(track["artists"]),
                    )
                    for track in self.__metadata["tracks"]
                ],
                ["cd", "#", "track name", "track artists"],
            ),
            end="\n\n",
        )

        # prompt user input for matching
        print()
        questionary.print(
            "Input the corresponding track for each of the following files:",
            style="bold fg:ansibrightgreen",
        )

        for i, file_name in enumerate(file_names_ext):
            chosen = questionary.autocomplete(
                f"{file_name}:",
                choices=scraped_tracks,
                qmark="[>]",
                default=scraped_tracks[best_match_inds[i]],
                validate=lambda res: res in scraped_tracks,
            ).ask()
            result[file_name] = scraped_tracks_dict[chosen]

        return result

    def __match_same(
        self,
        best_match_inds,
        best_scores,
        file_names_ext: List[str],
        scraped_tracks: List[str],
    ) -> dict[str, int]:
        # If there are duplicates, they need to be manually corrected in cmd line
        matched_track_names = []  # [(int, int, str, str, float)] for display
        unmatched_tracks = []  # unresolved track names
        unmatched_inds = []  # indices of unresolved track names
        unmatched_files = []  # unresolved file names

        x = [
            [] for _ in range(len(file_names_ext))
        ]  # x[i] is list of file inds matching track ind i
        for file_i, scraped_i in enumerate(best_match_inds):
            x[scraped_i].append(file_i)
        for scraped_i, file_inds in enumerate(x):
            # omitted track names - must be manually resolved
            if len(file_inds) == 0:
                unmatched_inds.append(scraped_i)
                unmatched_tracks.append(scraped_tracks[scraped_i])
                matched_track_names.append(
                    [
                        self.__metadata["tracks"][scraped_i]["disc"],
                        self.__metadata["tracks"][scraped_i]["num"],
                        scraped_tracks[scraped_i],
                        "*** UNMATCHED ***",
                        float("nan"),
                    ]
                )
            # track names used more than once - must be manually resolved
            elif len(file_inds) > 1:
                unmatched_inds.append(scraped_i)
                unmatched_tracks.append(scraped_tracks[scraped_i])
                unmatched_files += [file_names_ext[i] for i in file_inds]
                matched_track_names.append(
                    [
                        self.__metadata["tracks"][scraped_i]["disc"],
                        self.__metadata["tracks"][scraped_i]["num"],
                        scraped_tracks[scraped_i],
                        "*** UNMATCHED ***",
                        float("nan"),
                    ]
                )
            # 1-to-1 matches - automatic
            else:
                matched_track_names.append(
                    [
                        self.__metadata["tracks"][scraped_i]["disc"],
                        self.__metadata["tracks"][scraped_i]["num"],
                        scraped_tracks[scraped_i],
                        file_names_ext[file_inds[0]],
                        best_scores[file_inds[0]],
                    ]
                )

        matched_track_names.sort()

        # display matched and unmatched album tracks
        print()
        questionary.print(f"{self.__metadata["album_name"]}", style="bold underline")
        questionary.print(
            f"Auto-matched {len(file_names_ext) - len(unmatched_files)
                            } out of {len(file_names_ext)} tracks",
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

        if unmatched_files:
            print()
            questionary.print(
                "The following tracks could not be auto-matched:\n",
                style="bold",
            )
            print(
                tabulate(
                    zip(unmatched_tracks, unmatched_files),
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
                    choices=unmatched_files,
                    qmark="[>]",
                ).ask()
                matched_track_names[unmatched_inds[i]][3] = chosen
                unmatched_files.remove(chosen)

            print()
            print(
                tabulate(
                    matched_track_names,
                    ["cd", "#", "track name", "matched file", "similarity"],
                    floatfmt=".1f",
                ),
                end="\n\n",
            )

        return {field[3]: i for i, field in enumerate(matched_track_names)}

    def match(self) -> dict[str, int]:
        """Use RapidFuzz to match current songs with scraped track names

        Prompts for user input if matches can't be resolved

        args:
        - file_names: file names in specified directory, incl extensions
        - metadata: album metadata scraped from Apple Music

        returns:
        - List[str]: file names in track listing order

        """
        print("Matching songs...")
        file_names_ext = [
            file
            for file in os.listdir(
                self.__dest_path if self.__flag_extract else self.__album_path
            )
            if file.endswith(".mp3")
        ]
        file_names = [
            os.path.splitext(file_name_ext)[0] for file_name_ext in file_names_ext
        ]
        scraped_tracks_dict = {
            track["name"]: i for i, track in enumerate(self.__metadata["tracks"])
        }
        scraped_tracks = list(scraped_tracks_dict.keys())

        if len(file_names) > len(scraped_tracks):
            raise MismatchException(
                f"There are more MP3 files ({len(
                    file_names)}) than songs in the album ({len(scraped_tracks)})"
            )
        else:
            # Normalized indel distance of shorter string's optimal alignment in longer string
            # each row is a file name; each col is a scraped name
            # matrix values are scores btwn 0 and 100
            # see: https://rapidfuzz.github.io/RapidFuzz/Usage/process.html#cdist
            matrix = process.cdist(
                file_names, scraped_tracks, scorer=fuzz.partial_ratio
            )

            # Get the index of the max score in each row,
            # corresponding to the most similar scraped track name
            # best_match_inds[i] is most similar scraped_tracks index for index i in file_names
            best_match_inds = matrix.argmax(axis=1)

            # Get the similarity score of the most similar track name per row
            # best_scores[i] is highest score for index i in file_names
            best_scores = matrix.max(axis=1)

            if len(file_names) < len(scraped_tracks):
                return self.__match_less(
                    best_match_inds, file_names_ext, scraped_tracks_dict, scraped_tracks
                )
            else:
                return self.__match_same(
                    best_match_inds, best_scores, file_names_ext, scraped_tracks
                )

    def update(self, filenames_to_track_inds: dict[str, int]) -> None:
        """Update current songs' metadata

        Args
        - filenames_to_track_inds: file name -> track index (0-based)

        """
        update_path = self.__dest_path if self.__flag_extract else self.__album_path

        # prompt user input for confirmation
        print()
        proceed = questionary.confirm(
            "Proceed with updating files?", qmark="[>]", default=True, auto_enter=False
        ).ask()
        if not proceed:
            exit(1)
        print("Updating metadata (this may take some time)...")

        for file_name, i in filenames_to_track_inds.items():
            audio = ID3(os.path.join(update_path, file_name), v2_version=3)

            # set album name - TALB
            audio.setall(
                "TALB", [TALB(encoding=3, text=[self.__metadata["album_name"]])]
            )

            # set album artist(s) - TPE2
            audio.setall(
                "TPE2",
                [TPE2(encoding=3, text=[", ".join(self.__metadata["album_artists"])])],
            )

            # set album cover - APIC
            res = requests.get(self.__metadata["cover"])
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

            # set album genre - TCON
            audio.setall("TCON", [TCON(encoding=3, text=[self.__metadata["genre"]])])

            # set album year - TYER
            audio.setall("TYER", [TYER(encoding=3, text=[self.__metadata["year"]])])

            # set track name - TIT2
            audio.setall(
                "TIT2", [TIT2(encoding=3, text=[self.__metadata["tracks"][i]["name"]])]
            )

            # set track artist(s) - TPE1
            audio.setall(
                "TPE1",
                [
                    TPE1(
                        encoding=3,
                        text=[", ".join(self.__metadata["tracks"][i]["artists"])],
                    )
                ],
            )

            # set track num - TRCK
            audio.setall(
                "TRCK",
                [
                    TRCK(
                        encoding=3,
                        text=[
                            f"{self.__metadata["tracks"][i]["num"]
                            }/{self.__metadata["tracks"][i]["total_num"]}"
                        ],
                    )
                ],
            )

            # set track disc num - TPOS
            audio.setall(
                "TPOS",
                [
                    TPOS(
                        encoding=3,
                        text=[
                            f"{self.__metadata["tracks"][i]["disc"]
                            }/{self.__metadata["tracks"][i]["total_disc"]}"
                        ],
                    )
                ],
            )

            # save metadata
            audio.save(v2_version=3)

            # rename track file
            if not self.__flag_preserve_songs:
                new_file_name = (
                    " ".join(
                        re.sub(
                            r'[<>:"\/\\\|\?\*]',
                            "",
                            self.__metadata["tracks"][i]["name"],
                        ).split()
                    )
                    + ".mp3"
                )
                os.rename(
                    os.path.join(update_path, file_name),
                    os.path.join(update_path, new_file_name),
                )

        # rename album folder
        if not self.__flag_preserve_album:
            new_album_name = " ".join(
                re.sub(r'[<>:"\/\\\|\?\*]', "", self.__metadata["album_name"]).split()
            )
            os.rename(
                update_path,
                os.path.join(
                    os.path.dirname(os.path.normpath(update_path)), new_album_name
                ),
            )

        print("✨ Done! ✨")
        print(f"Successfully updated {len(filenames_to_track_inds)} songs.")


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
        "--preserve-album",
        action="store_true",
        help="do not change album folder name\nif -x, do not change dest_path folder name",
    )
    parser.add_argument(
        "-s",
        "--preserve-songs",
        action="store_true",
        help="do not change song file names",
    )

    args = parser.parse_args()

    # check for well-formed extract syntax
    if args.extract and args.dest_path is None:
        parser.error("-x requires dest_path (path to unzipped folder)")
    if not args.extract and args.dest_path:
        parser.error("dest_path requires -x flag")

    formatter = Formatter(
        args.album_path,
        args.dest_path,
        args.AM_album_link,
        args.extract,
        args.preserve_album,
        args.preserve_songs,
    )
    formatter.run()


if __name__ == "__main__":
    main()
