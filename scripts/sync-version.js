#!/usr/bin/env node
/**
 * Syncs VERSION file to manifest.json.
 * Run before committing a release.
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const versionFile = path.join(root, "VERSION");
const manifestPath = path.join(root, "custom_components", "camstack", "manifest.json");

const version = fs.readFileSync(versionFile, "utf8").trim();
if (!version) {
  console.error("VERSION file is empty");
  process.exit(1);
}

// Update manifest.json
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
manifest.version = version;
fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
console.log("Updated manifest.json:", version);

// Update addon config.yaml (if exists, addon is gitignored)
const addonConfigPath = path.join(root, "addon", "camstack", "config.yaml");
if (fs.existsSync(addonConfigPath)) {
  let config = fs.readFileSync(addonConfigPath, "utf8");
  config = config.replace(/^version:\s*["']?[\d.]+["']?/m, `version: "${version}"`);
  fs.writeFileSync(addonConfigPath, config);
  console.log("Updated addon config.yaml:", version);
}

// Update package.json
const pkgPath = path.join(root, "package.json");
if (fs.existsSync(pkgPath)) {
  const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
  pkg.version = version;
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");
  console.log("Updated package.json:", version);
}
