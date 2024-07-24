# 💿 mp3-album-formatter

a command line tool to update MP3 metadata, folders, and file names according to
Apple Music

> [!IMPORTANT] This script is designed specifically to work on **MP3 files
> only**. MP3 files in the album must have already have file names or track name
> metadata similar to the actual track names listed on Apple Music.

## What it does

- Updates ID3 metadata for each MP3 song in the specified album folder according
  to Apple Music, including:

  - album name
  - album artist(s)
  - album cover (512x512 jpg)
  - album genre
  - album year
  - track name
  - track artist(s)
  - track number
  - disc number

- **Modifiable behavior** (see [Options](#options) for details):

  - By default, updates album folder name to match album name
    - `-a` flag keeps current folder name unchanged
    - `-A "<format>"` allows custom folder name
  - By default, updates song file names to match track names
    - `-s` flag keeps current file names unchanged
    - `-S "<format>"` allows custom file names
  - By default, treats album folder as unzipped
    - `-x` flag unzips album ZIP to a specified destination folder
  - By default, uses file names to match album tracks
    - `-m` flag uses each file's existing track name metadata

## How to use

### Prerequisites

- All of the songs you want to update should belong to the same album and be
  contained in a single folder or ZIP. Any songs within nested folders will be
  moved to the root album folder.
- The number of songs in the album folder must be <= the total number of tracks
  in the album.
- Song file names and/or existing track name metadata should be close to
  (doesn't need to be exact) their track names listed on Apple Music -- this is
  so that the script can match files with the correct metadata.

### Setup

1. `git clone` this repo
2. `cd` into the folder
3. run `pip install requirements.txt`

### Usage

#### General Usage

```
python3 formatter.py [options] <album_path> [<dest_path>] <AM_album_link>
```

#### Arguments

- `<album_path>`: **relative** path to album folder or album ZIP. This folder or
  ZIP contains all MP3 files you want to update.
- `<dest_path>`: **relative** path to unzipped destination folder, only required
  if `-x` (extract) option specified
- `<AM_album_link>`: full URL to album page on Apple Music Web Player (ex.
  https://music.apple.com/us/album/thriller/269572838)

#### Options

```
-x, --extract           extract songs from <album_path> ZIP file to <dest_path>
                        creates destination folder if it does not exist

-m, --use-metadata      use existing track name metadata instead of file name to match files to album tracks

-a, --preserve-album    keep album folder name the same;
                        without any options, default behavior is to rename album folder to match album name

-s, --preserve-songs    keep all song file names the same;
                        without any options, default behavior is to rename all song files to match track names

-A "<format>", --album-name-format "<format>"
                        specify custom format for renaming album folder name:
                          %a - album name
                          %r - album artist(s)
                          %g - album genre
                          %y - album year
                        use quotes around the format:
                          ex. -A "%r - %a"  ==>  folder name: "Michael Jackson - Thriller"
                        without this option, default format is "%a"

-S "<format>", --song-name-format "<format>"
                        specify custom format for renaming song file names:
                          %t - track name
                          %s - add'l track artist(s)
                          %n - track number w/ leading 0 for single digits, ex. '01'
                          %d - track disc number
                          %a - album name
                          %r - album artist(s)
                          %g - album genre
                          %y - album year
                        use quotes around the format, and do not include '.mp3':
                          ex. -S "%d.%n - %t"  ==>  file name: "1.01 - Wanna Be Startin' Somethin'.mp3"
                        without this option, default format is "%t"
```

#### Examples

##### Standard usage

- This will match all song files in `../album_folder` to the tracks in the
  album, update their metadata, then rename the folder to match the album name
  (`../Thriller`)
- All song files will be renamed to their track names (ex.
  `Wanna Be Startin' Something'.mp3`)

```
python3 formatter.py ../album_folder https://music.apple.com/us/album/thriller/269572838
```

##### Unzip/extract album

- This will extract `../album.zip` to the specified location
  (`../music/dest_folder`), then rename it to match the album name
  (`../music/Thriller`)
- To preserve the specified destination folder name of `dest_folder`, you must
  also use `-a`

```
python3 formatter.py -x ../album.zip ../music/dest_folder https://music.apple.com/us/album/thriller/269572838
```

##### Preserve album folder and song file names

```
python3 formatter.py -as ../album_folder https://music.apple.com/us/album/thriller/269572838
```

##### Custom formatting for album folder and song file names

- This will rename `../album_folder` to `../Thriller (1982)`
- Each song file will be renamed to something like
  `Michael Jackson - Wanna Be Startin' Somethin'.mp3`

```
python3 formatter.py -A "%a (%y)" -S "%r - %t" ../album_folder https://music.apple.com/us/album/thriller/269572838
```

## How it works

1. Scrapes album metadata from Apple Music Web Player using
   [Playwright](https://playwright.dev/python/)
2. Reads MP3 files in album folder
3. Matches files to appropriate metadata using
   [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) and user input where
   files can't be auto-matched
4. Updates each file's ID3 metadata using
   [Mutagen](https://github.com/quodlibet/mutagen)
5. Optionally updates file names and album folder name according to specified
   format

## Packages used

Playwright, Mutagen, RapidFuzz, Questionary, Tabulate
