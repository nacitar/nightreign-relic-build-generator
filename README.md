# NightReign Relic Build Generator
This utility is used to find combinations of your relics which can be equipped
together in order to make a desirable build.

# How It Works
Your relics are determined by parsing your save file.  What constitutes a
"good" build, though, is decided by the user via a simple json file that
associates a score value with effects that are desirable.  Each combination
of relics is checked against types of urns that are available to your selected
nightfarer and only valid combinations are provided.  Among the valid
combinations only the top N scoring builds are provided.

# The Score Format
Examples of the score format are provided and bundled into the tool for usage.
You can see them in the [resources](https://github.com/nacitar/nightreign-relic-build-generator/tree/main/src/nightreign_relic_build_generator/resources)
directory, in the files named like `scores_<LABEL>.json`.  These are not meant
to encapsulate every possible build approach, or any particular meta build...
they are simply scores I used for testing the utility for my own purposes.

The format is extremely simple.  You determine all relic effects that would
be relevant in choosing a relic combination for your build and provide an
integral score to associate with it.  You can either specify the base name of
the effect (e.g. `Improved Critical Hits`) or you can also specify a specific
level of the effect to associate with the score (e.g.
`Improved Critical Hits +1` or `Improved Critical Hits +0` for the non-plussed
version).  If you choose to not specify a +<level> suffix, the tool assigns
your score and *multiplies* it by (1+level); so +0 gets your score while +1
is double your score and +2 is triple your score, etc...

# Efficiency
Generating permutations of large collections of relics can be quite slow.  For
this reason, some strategies are employed in order to reduce the solution set.
Any relic that individually doesn't have at least a score matching the one
passed via -p/--prune is not even considered.  The default for this value is
`1`, which makes it so only relics that provide something you care about are
considered.  Thus, the fewer effects you've provided scores for the less
permutations will be generated and thus the faster the answer will be provided.
Some more advanced techniques can also be utilized, too.  Imagine that you set
all effects you care about to have a score of 10 or more; you could pass
`-p 20` and only relics that have TWO desired effects will be considered.  This
may not provide as good of a build if you have poor relics available to you,
but it is an option nonetheless.

You can also provide negative scores to any undesirable effects, but do keep in
mind the pruning process and how it functions if you do that; I would generally
advise against it as it could remove otherwise good relics from the pool.

# Usage
This section assumes that the project is available and can be invoked via the
alias `app`, which is how it would work if you were using the *poetry* tool to
manage the project.  If you set it up in other ways, you may instead be
invoking it as a python module `python3 -m nightreign_relic_build_generator`.
In either case, this section discusses the CLI arguments.  For setup, look in
the *Setup* section.


The tool has several subcommands and can generally be invoked via:
```bash
app <subcommand>
# with extra logging
app -v <subcommand>
```

The subcommands are documented below.

## Simple Builtins
### list-builtins
This subcommand simply lists the names of the builtin score tables included in
the tool. Invoke it via:
```bash
app list-builtins
```
## Builtins Requiring a Save File
There are common arguments for commands that require a save file.  Obviously
the path to the `.sl2` file itself must be provided.  By default, the tool
looks at the first save slot in that file (index 0).  However, you can use the
`-i/--index` argument to specify a different index.  Indexes 0-9 refer to save
slots 1-10.

### dump-relics
This subcommand doesn't find any builds, it simply dumps the relics found in
your save file.  Even though the save slot index defaults to 0, here's an
example explicitly setting it to 0 as an example:
```bash
app dump-relics YourSave.sl2 -i 0
```
The output will also report any relics whose item ids are not recognized or
have any effect ids that aren't recognized.  Without a recognized item id, the
color of the relic cannot be determined and thus it cannot be considered.  If
any effects are missing on the relic, the score for it would also be invalid.

When encountering incomplete relics, the best course of action is to open the
relic list in game and searching for the effects that *are* recognized.  If you
find the relic, you can then see its name, color, and the missing effects.  If
an issue is created on this repository (or even better, a pull request) that
gives these missing item id/effect id to name mappings I will add them.

### compute
This subcommand finds optimal builds according to the scores provided.  The
relevant arguments are listed below.

ONE of these score file arguments:
- -s/--scores, the path to your scores file
- -b/--builtin-scores, the name of one of the builtin score files

Non-exclusive arguments:
- -c/--character-class, the name of the class whose urns are used
- -l/--limit, the number of top-scoring builds to keep (default 50)
- -n/--no-deep, pass this flag to score builds leaving deep relic slots empty
- --no-color, disabled colorized output (NOTE: colors are Linux only)

Power-user options you probably don't want to change:
- -m/--minimum, the minimum score requires to keep a build (default 1)
- -p/--prune, the minimum score a relic must have to be considered (default 1)

An example invocation using the urns for raider and using the builtin score
table I've created as an example for raider:
```bash
app compute YourSave.sl2 -c raider -b raider
```

# Setup
## poetry
This project is configured to be managed by [Poetry](https://python-poetry.org/docs/#installation).
There are other ways you can set things up but they won't be discussed or
supported here.  Once your system has poetry availabe and in PATH:
```bash
# from the project directory tree; install is only needed once per pull
poetry install
poetry run app <arguments>
```
