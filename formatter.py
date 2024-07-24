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


class InvalidFormatException(Exception):
    pass


class Formatter:
    def __init__(
        self,
        album_path: str,
        dest_path: str,
        album_link: str,
        extract: bool,
        use_metadata: bool,
        preserve_album_name: bool,
        preserve_song_names: bool,
        album_name_format: str | None,
        song_name_format: str | None,
    ):
        """Constructs Formatter object.

        Args:
            album_path (str): relative path to album folder or ZIP
            dest_path (str): relative path to unzipped destination folder
            album_link (str): link to Apple Music album page
            extract (bool): extract album from ZIP
            use_metadata (bool): use metadata instead of file name for matching
            preserve_album_name (bool): preserve album folder name
            preserve_song_names (bool): preserve song file names
            album_name_format (str | None): custom album folder name format
            song_name_format (str | None): custom song file name format

        Raises:
            InvalidFormatException: user input invalid album folder or song
              file name format
            FileNotFoundError: album_path does not exist
        """
        if album_name_format == "" or song_name_format == "":
            raise InvalidFormatException("Invalid name format: empty string")

        if os.path.exists(album_path):
            self.__album_path = album_path
        else:
            raise FileNotFoundError(f"Could not find {album_path}")

        self.__album_name_format = (
            "%a" if album_name_format is None else album_name_format
        )
        self.__song_name_format = "%t" if song_name_format is None else song_name_format
        self.__dest_path = dest_path
        self.__album_link = album_link
        self.__flag_extract = extract
        self.__flag_use_metadata = use_metadata
        self.__flag_preserve_album_name = preserve_album_name
        self.__flag_preserve_song_names = preserve_song_names
        self.__update_path = (
            self.__dest_path if self.__flag_extract else self.__album_path
        )
        self.__metadata = {}

    def run(self) -> None:
        try:
            self.unzip()
            self.flatten()
            self.scrape()
            filenames_to_track_inds = self.match()
            self.update(filenames_to_track_inds)
        except Exception as e:
            # delete unzipped folder
            if self.__dest_path and os.path.isdir(self.__dest_path):
                shutil.rmtree(self.__dest_path)
            print(e)

    def unzip(self) -> None:
        """Extracts album_path ZIP to dest_path."""
        if self.__flag_extract:
            print("Unzipping file...")
            with ZipFile(self.__album_path, "r") as z:
                z.extractall(self.__dest_path)

    def delete_zip(self) -> None:
        """Prompts user to delete original album_path ZIP."""
        if self.__flag_extract:
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
        """Moves all files out of nested folders into root album folder.

        Deletes all nested folders after moving files.
        """
        root_folder = self.__update_path
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

        Returns:
            dict: self.__metadata
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
        existing_metadata_titles: List[str],
    ) -> dict[str, int]:
        """Matches files to tracks when there are less files than album tracks.

        Args:
            best_match_inds (ndarray): length = # files, best_match_inds[i] is
              most similar scraped_tracks index for index i in file_names_ext
            file_names_ext (List[str]): file names with extensions (.mp3)
            scraped_tracks_dict (dict[str, int]): scraped track name -> track
              index (0-based)
            scraped_tracks (List[str]): scraped track names
            existing_metadata_titles (List[str]): files' existing ID3 track
              names

        Returns:
            dict[str, int]: file name -> track index (0-based)
        """
        # prompt user input for confirmation
        proceed = questionary.confirm(
            f"""There are less MP3 files ({len(file_names_ext)}) than songs in the album ({
                len(scraped_tracks)}). Proceed?""",
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
                ["cd", "#", "track name", "add'l track artists"],
            ),
            end="\n\n",
        )

        # prompt user input for matching
        print()
        questionary.print(
            "Input the corresponding track for each of the following files:",
            style="bold fg:ansibrightgreen",
        )

        scraped_tracks_copy = [t for t in scraped_tracks]

        for i, file_name in enumerate(file_names_ext):
            chosen = questionary.autocomplete(
                f"""{file_name}{" [" + existing_metadata_titles[i] +
                              "]" if self.__flag_use_metadata else ""}:""",
                choices=scraped_tracks_copy,
                qmark="[>]",
                default=(
                    scraped_tracks[best_match_inds[i]]
                    if scraped_tracks[best_match_inds[i]] in scraped_tracks_copy
                    else ""
                ),
                validate=lambda res: res in scraped_tracks_copy,
            ).ask()
            scraped_tracks_copy.remove(chosen)
            result[file_name] = scraped_tracks_dict[chosen]

        return result

    def __match_same(
        self,
        best_match_inds,
        best_scores,
        file_names_ext: List[str],
        scraped_tracks: List[str],
        existing_metadata_titles: List[str],
    ) -> dict[str, int]:
        """Matches files to tracks when there are as many files as album tracks.

        Args:
            best_match_inds (ndarray): length = # files, best_match_inds[i] is
              most similar scraped_tracks index for index i in file_names_ext
            best_scores (ndarray): length = # files, best_scores[i] is highest
              score for index i in file_names
            file_names_ext (List[str]): file names with extensions (.mp3)
            scraped_tracks (List[str]): scraped track names
            existing_metadata_titles (List[str]): files' existing ID3 track
              names

        Returns:
            dict[str, int]: file name -> track index (0-based)
        """
        # If there are duplicates, they need to be manually corrected in cmd line
        matched_track_names = []  # [(int, int, str, str, float)] for display
        unmatched_tracks = []  # unresolved track names
        unmatched_inds = []  # indices of unresolved track names
        unmatched_files = []  # unresolved file names
        unmatched_existing_metadata_titles = []

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
                        "*** UNMATCHED ***",
                        float("nan"),
                    ]
                )
            # track names used more than once - must be manually resolved
            elif len(file_inds) > 1:
                unmatched_inds.append(scraped_i)
                unmatched_tracks.append(scraped_tracks[scraped_i])
                unmatched_files += [file_names_ext[i] for i in file_inds]
                unmatched_existing_metadata_titles += [
                    existing_metadata_titles[i] for i in file_inds
                ]
                matched_track_names.append(
                    [
                        self.__metadata["tracks"][scraped_i]["disc"],
                        self.__metadata["tracks"][scraped_i]["num"],
                        scraped_tracks[scraped_i],
                        "*** UNMATCHED ***",
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
                        file_names_ext[file_inds[0]]
                        + (
                            f" [{existing_metadata_titles[file_inds[0]]}]"
                            if self.__flag_use_metadata
                            else ""
                        ),
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
                [(m[0], m[1], m[2], m[4], m[5]) for m in matched_track_names],
                ["cd", "#", "track name", "matched file [existing name]", "similarity"],
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

            choices = []
            choices_to_files_dict = {}
            for i in range(len(unmatched_files)):
                choice = unmatched_files[i] + (
                    f" [{unmatched_existing_metadata_titles[i]}]"
                    if self.__flag_use_metadata
                    else ""
                )
                choices.append(choice)
                choices_to_files_dict[choice] = unmatched_files[i]

            for i in range(len(unmatched_tracks)):
                chosen = questionary.select(
                    f"{unmatched_tracks[i]}",
                    choices=choices,
                    qmark="[>]",
                ).ask()
                matched_track_names[unmatched_inds[i]][3] = choices_to_files_dict[
                    chosen
                ]
                matched_track_names[unmatched_inds[i]][4] = chosen
                choices.remove(chosen)

            print()
            print(
                tabulate(
                    [(m[0], m[1], m[2], m[4], m[5]) for m in matched_track_names],
                    [
                        "cd",
                        "#",
                        "track name",
                        "matched file [existing name]",
                        "similarity",
                    ],
                    floatfmt=".1f",
                ),
                end="\n\n",
            )

        return {field[3]: i for i, field in enumerate(matched_track_names)}

    def match(self) -> dict[str, int]:
        """Matches files to album tracks.

        Uses RapidFuzz to match file names or existing track name metadata with
        scraped track names.

        Raises:
            MismatchException: more files than album tracks

        Returns:
            dict[str, int]: file name -> track index (0-based)
        """
        print("Matching songs...")
        file_names_ext = [
            file for file in os.listdir(self.__update_path) if file.endswith(".mp3")
        ]
        file_names = [
            os.path.splitext(file_name_ext)[0] for file_name_ext in file_names_ext
        ]
        existing_metadata_titles = []
        for file_name in file_names_ext:
            audio = ID3(os.path.join(self.__update_path, file_name), v2_version=3)
            TIT2_obj = audio.get("TIT2")
            title = ""
            if TIT2_obj is not None:
                title = TIT2_obj.text[0]
            existing_metadata_titles.append(title)
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
                existing_metadata_titles if self.__flag_use_metadata else file_names,
                scraped_tracks,
                scorer=fuzz.partial_ratio,
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
                    best_match_inds,
                    file_names_ext,
                    scraped_tracks_dict,
                    scraped_tracks,
                    existing_metadata_titles,
                )
            else:
                return self.__match_same(
                    best_match_inds,
                    best_scores,
                    file_names_ext,
                    scraped_tracks,
                    existing_metadata_titles,
                )

    def format_album_name(self) -> str:
        """Creates new album folder name based on specified album_name_format

        Returns:
            str: new album folder name
        """
        name_format_dict = {
            "%a": self.__metadata["album_name"],
            "%r": ", ".join(self.__metadata["album_artists"]),
            "%g": self.__metadata["genre"],
            "%y": self.__metadata["year"],
        }
        format = self.__album_name_format
        for mod, replaced in name_format_dict.items():
            format = format.replace(mod, replaced)
        return format

    def format_song_names(self, matched_track_inds: List[int]) -> List[str]:
        """Creates new song file names based on specified song_name_format

        Returns:
            List[str]: new song file names in same order as matched_track_inds;
              names do not include .mp3 extension
        """
        name_format_dict = {
            "%a": self.__metadata["album_name"],
            "%r": ", ".join(self.__metadata["album_artists"]),
            "%g": self.__metadata["genre"],
            "%y": self.__metadata["year"],
        }
        formatted_names = []
        for track_ind in matched_track_inds:
            name_format_dict["%t"] = self.__metadata["tracks"][track_ind]["name"]
            name_format_dict["%s"] = ", ".join(
                self.__metadata["tracks"][track_ind]["artists"]
            )
            name_format_dict["%n"] = str(
                self.__metadata["tracks"][track_ind]["num"]
            ).zfill(2)
            name_format_dict["%d"] = str(self.__metadata["tracks"][track_ind]["disc"])

            format = self.__song_name_format
            for mod, replaced in name_format_dict.items():
                format = format.replace(mod, replaced)
            formatted_names.append(format)

        return formatted_names

    def update(self, filenames_to_track_inds: dict[str, int]) -> None:
        """Update song file metadata and rename album folder, song files

        Args:
            filenames_to_track_inds (dict[str, int]): file name -> track index
              (0-based)
        """
        # prompt user input for confirmation
        print()
        proceed = questionary.confirm(
            "Proceed with updating files?", qmark="[>]", default=True, auto_enter=False
        ).ask()
        if not proceed:
            exit(1)
        print("Updating metadata (this may take some time)...")

        for file_name, i in filenames_to_track_inds.items():
            audio = ID3(os.path.join(self.__update_path, file_name), v2_version=3)

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

        # rename track files
        if not self.__flag_preserve_song_names:
            print("Renaming song files...")
            formatted_names = self.format_song_names(
                list(filenames_to_track_inds.values())
            )
            if len(formatted_names) == len(set(formatted_names)):
                for i, file_name in enumerate(filenames_to_track_inds.keys()):
                    new_file_name = (
                        re.sub(r'[<>:"\/\\\|\?\*]', "", formatted_names[i]) + ".mp3"
                    )
                    os.rename(
                        os.path.join(self.__update_path, file_name),
                        os.path.join(self.__update_path, new_file_name),
                    )
            else:
                questionary.print(
                    "Using the specified song name format, some files will have the same name. Aborting song file renaming.",
                    style="bold fg:red",
                )

        # rename album folder
        if not self.__flag_preserve_album_name:
            print("Renaming album folder...")
            new_album_name = re.sub(r'[<>:"\/\\\|\?\*]', "", self.format_album_name())
            os.rename(
                self.__update_path,
                os.path.join(
                    os.path.dirname(os.path.normpath(self.__update_path)),
                    new_album_name,
                ),
            )

        self.delete_zip()
        print("✨ Done! ✨")
        print(f"Successfully updated {len(filenames_to_track_inds)} songs.")


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Updates album and song metadata\nhttps://github.com/evxiong/mp3-album-formatter",
        usage="python3 formatter.py [options] <album_path> [<dest_path>] <AM_album_link>",
    )
    parser.add_argument(
        "album_path",
        metavar="<album_path>",
        help="Relative path to album folder or album ZIP; this folder or\nZIP contains all MP3 files you want to update",
    )
    parser.add_argument(
        "dest_path",
        nargs="?",
        default=None,
        metavar="<dest_path>",
        help="Relative path to unzipped destination folder, only required\nif `-x` (extract) option specified",
    )
    parser.add_argument(
        "AM_album_link",
        metavar="<AM_album_link>",
        help="Full URL to album page on Apple Music Web Player (ex.\nhttps://music.apple.com/us/album/thriller/269572838)",
    )
    parser.add_argument(
        "-x",
        "--extract",
        action="store_true",
        help="Extract songs from <album_path> ZIP file to <dest_path>;\ncreates destination folder if it does not exist",
    )
    parser.add_argument(
        "-m",
        "--use-metadata",
        action="store_true",
        help="Use existing track name metadata instead of file name to\nmatch files to album tracks",
    )
    parser.add_argument(
        "-a",
        "--preserve-album-name",
        action="store_true",
        help="Keep current album folder name unchanged; without any\noptions, renames album folder to match album name",
    )
    parser.add_argument(
        "-s",
        "--preserve-song-names",
        action="store_true",
        help="Keep current file names unchanged; without any options,\nrenames all song files to match track names",
    )
    parser.add_argument(
        "-A",
        "--album-name-format",
        metavar='"<format>"',
        help="""Specify custom format for renaming album folder name:\n    %%a - album name\n    %%r - album artist(s)\n    %%g - album genre\n    %%y - album year\nUse quotes around the format:\n    ex. -A "%%r - %%a"\n        ==>  folder name: "Michael Jackson - Thriller"\nDefault format is "%%a" """,
    )
    parser.add_argument(
        "-S",
        "--song-name-format",
        metavar='"<format>"',
        help="""Specify custom format for renaming song file names:\n    %%t - track name\n    %%s - add'l track artist(s)\n    %%n - track number w/ leading 0 for single digits, ex. '01'\n    %%d - track disc number\n    %%a - album name\n    %%r - album artist(s)\n    %%g - album genre\n    %%y - album year\nUse quotes around the format, and do not include '.mp3':\n    ex. -S "%%d.%%n - %%t"\n        ==>  file name: "1.01 - Wanna Be Startin' Somethin'.mp3"\nDefault format is "%%t" """,
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
        args.use_metadata,
        args.preserve_album_name,
        args.preserve_song_names,
        args.album_name_format,
        args.song_name_format,
    )
    formatter.run()


if __name__ == "__main__":
    main()
