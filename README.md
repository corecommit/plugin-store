# Plugin Store

Community plugin repository for [Infinite Yield](https://github.com/corecommit/infiniteyield). Browse and install plugins in-game with the `pluginstore` command.

## Structure

```
plugins/         — Individual .iy plugin files
plugins.json     — Plugin manifest (name, author, description, download URL)
pluginstore.iy   — Core plugin that powers the in-game store UI, handles plugin fetching, search, and installation
```

## Submitting a Plugin

1. Add your plugin as a `.iy` file to the `plugins/` directory.
2. Add an entry to `plugins.json`:

```json
{
  "author": "yourname",
  "name": "YourPlugin.iy",
  "description": "What your plugin does."
  "url": "https://raw.githubusercontent.com/corecommit/plugin-store/main/plugins/YourPlugin.iy",
}
```

3. Open a pull request.
