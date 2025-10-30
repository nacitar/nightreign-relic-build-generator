# 📦 Using Smithbox to Export Text and Param Data for *Elden Ring: Nightreign*

This guide explains how to use **Smithbox** (via Wine on Linux) to export two data files that can then be parsed by your `parse.py` script into the format used by `relic-ids.json`.

---

## 🧰 Prerequisites

- **Smithbox** (Windows executable)  
- If on Linux, **Wine** installed on your system  
- A copy of *Elden Ring: Nightreign* installed (via Steam or otherwise)  
- `parse.py` from the same directory as this README.

---

## 🚀 Step 1 — Launch Smithbox

Run Smithbox directly if on windows or if on Linux, using wine:

```bash
wine Smithbox.exe
```

---

## 🪶 Step 2 — Create a New Project

1. In the menubar, select **Project → New Project**  
2. Fill in the fields:
   - **Name:** whatever you want, e.g. `Nightreign`
   - **Project Type:** `Elden Ring: Nightreign`
   - **Project Directory:** choose any location (e.g. `Z:\home\USERNAME\smithbox`)
   - **Data Directory:**  
     `Z:\home\USERNAME\.local\share\Steam\steamapps\common\ELDEN RING NIGHTREIGN\Game`
3. Click **Create**

---

## 📜 Step 3 — Export Item Text Data

1. From the menubar, open **Text Editor**
2. In the **left pane**, navigate to:  
   `English (US) → Item`
3. From the menubar, select:  
   **Data → Export → File → Export Selected File**
4. When prompted:
   - Enter a filename **without an extension**, e.g. `strings`
5. Smithbox will write the file to:

   ```
   [project directory]/.smithbox/Workflow/Exported Text/strings.json
   ```

---

## ⚙️ Step 4 — Export Useful Params

The sections you want to export, and the filenames I suggest for them:
|   Section Name    |  Provides   | Filename |
|-------------------|-------------|----------|
| EquipParamAntique |  Relic IDs  | antique  |
| AntiqueStandParam | Vessel info |  stand   |


1. From the menubar, open **Param Editor**
2. In the search box at the top of the left pane, type the name of the section. 
3. Click the result to open it and ensure you can see its values in the **Rows** pane.
4. From the menubar, select:  
   **Data → Export CSV → All Rows → Export to File… → Export All Fields**
5. Save the file wherever you like, using a clear name such as the one suggested.
NOTE: you do not need to type in the extension; these will be .csv files.

---

## 🐍 Step 5 — Parse the Exported Data

After you have all files, place them in your CWD and run the parsing script:

```bash
./parse.py
```

This will process the exported data and generate output in the format used by `relic-ids.json`.

---

## ✅ Summary of Output Locations

| File | Source  | Default Export Path |
|------|---------|---------------------|
| `strings.json` | Text Editor export  | `<project>/.smithbox/Workflow/Exported Text/strings.json` |
| `antique.csv`  | Param Editor export | user-chosen (e.g. `~/smithbox/antique.csv`) |
| `stand.csv`    | Param Editor export | user-chosen (e.g. `~/smithbox/stand.csv`) |

---

### 📝 Notes

- Both exported files must come from the same game version to ensure data consistency.  
- Paths like `Z:\home\USERNAME\…` correspond to your Linux filesystem through Wine’s drive mapping.  
- If generating due to a Nightreign update, re-export both files before re-running the parser.
