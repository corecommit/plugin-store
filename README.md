# Plugin Store

Community plugin repository for [Infinite Yield](https://github.com/YeildFE/infiniteyield). Browse and install plugins in-game with the `pluginstore` command.

## Structure

```
plugins/         — Individual .iy plugin files
plugins.json     — Plugin manifest (name, author, description, download URL)
pluginstore.iy   — Actual Plugin used in infinite yeild
```

## Submitting a Plugin

1. Add your plugin as a `.iy` file to the `plugins/` directory.
2. Add an entry to `plugins.json`:

```json
{
  "author": "yourname",
  "name": "YourPlugin.iy",
  "description": "What your plugin does."
  "url": "https://raw.githubusercontent.com/YeildFE/plugin-store/main/plugins/YourPlugin.iy",
}
```

3. Open a pull request.
