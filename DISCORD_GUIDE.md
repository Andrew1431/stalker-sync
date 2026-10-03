# stalker-sync: beginner guide (copy into Discord)

**Problem:** co-op says "mod config mismatch", or friends have different versions of the same mods.
**Fix:** one person (the **REFERENCE**) makes everyone else's files match theirs. It takes about 5 minutes.

---

**STEP 0: Everyone sets up (once)**
1. Install Python from https://www.python.org/downloads/. On Windows, **tick "Add python.exe to PATH"** in the installer. Linux usually already has it.
2. Download `stalker_sync.py` from the GitHub page.
3. Put it in your **GAMMA folder**: the folder that contains `ModOrganizer.exe`.

**STEP 1: Check whether you even need this**
1. Each player starts the game to the main menu, then quits.
2. Each player double-clicks `stalker_sync.py` and picks **1**.
3. Everyone posts their `config` and `spawn` numbers in chat.
- ✅ All numbers the same → you're synced. Go play.
- ❌ Different → continue.

**STEP 2: Pick the REFERENCE player**
Pick whoever's game works best. Everyone else will be made to match them.

**STEP 3: Everyone scans**
1. Double-click `stalker_sync.py` and pick **2**.
2. Type your name, for example `bob`.
3. A file such as `bob.json` appears next to the script.
4. **Everyone except the reference** sends their `.json` to the reference, by Discord DM or any other way. It's small.
The reference also does this step, for their own `.json`.

**STEP 4: REFERENCE builds a patch for each friend**
1. Double-click `stalker_sync.py` and pick **4**.
2. When it asks for **YOUR .json**, drag your own `.json` onto the window and press Enter.
3. When it asks for **Friend's .json**, drag the friend's file in and press Enter.
4. A file like `stalker-sync-patch-bob.zip` appears. Send it to that friend.
5. Do not change mods between Step 3 and Step 4.

**STEP 5: Friend applies the patch**
1. Close the game **and Mod Organizer**.
2. Double-click `stalker_sync.py` and pick **5**.
3. Drag the patch `.zip` onto the window and press Enter.
4. It first lists what it **would** change. Type `yes` to apply.
5. Always start the game **through Mod Organizer**, because the fixes live in MO2's "Overwrite".

**STEP 6: Check**
Everyone repeats Step 1. The numbers should now match. 🎉

---

**Something went wrong?**
- 🔸 *"could not find your G.A.M.M.A. folder"*: the script isn't in the folder with `ModOrganizer.exe`.
- 🔸 *Window flashes and closes / "python not found"*: Python isn't installed or wasn't added to PATH. Reinstall it and tick that box.
- 🔸 *The scan says mods differ*: tick or untick those mods in Mod Organizer so everyone matches, then scan again.
- 🔸 *Undo*: the files that were replaced or removed are in `stalker-sync-backup/` inside the GAMMA folder.
- 🔸 *"protocol version mismatch"*: you have different xrRazom versions. Everyone must install the same release. Syncing files can't fix this.

**🐧 Linux players**
Run it from a terminal inside your GAMMA folder:
```
python3 stalker_sync.py
```
The menu works the same way. Paste paths instead of dragging.
