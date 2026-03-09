#!/usr/bin/env node
/**
 * Bumps VERSION (patch/minor/major) and syncs to manifest and addon config.
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const versionFile = path.join(root, "VERSION");
const bump = process.argv[2] || "patch";

if (!["patch", "minor", "major"].includes(bump)) {
  console.error("Usage: node bump-version.js [patch|minor|major]");
  process.exit(1);
}

let version = fs.readFileSync(versionFile, "utf8").trim();
const parts = version.split(".").map(Number);
if (parts.length !== 3 || parts.some(isNaN)) {
  console.error("VERSION must be semver (e.g. 0.1.0)");
  process.exit(1);
}

if (bump === "patch") parts[2]++;
else if (bump === "minor") { parts[1]++; parts[2] = 0; }
else if (bump === "major") { parts[0]++; parts[1] = 0; parts[2] = 0; }

version = parts.join(".");
fs.writeFileSync(versionFile, version + "\n");
console.log("Bumped to", version);

require("./sync-version.js");
