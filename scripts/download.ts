import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import readline from "readline/promises";
import { fileURLToPath } from "url";
import { kebabCase } from "change-case";

const publicDir = "./public/audio";

async function ask(question: string): Promise<string> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  try {
    return (await rl.question(question)).trim();
  } finally {
    rl.close();
  }
}

// Kebab-case each part, keeping "_" as the artist/title separator.
function toFileName(name: string): string {
  return name
    .split("_")
    .map((part) => kebabCase(part.replace(/['’]/g, "")))
    .filter(Boolean)
    .join("_");
}

const noiseTag =
  /\s*[([][^)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|remaster(ed)?|mv)\b[^)\]]*[)\]]/gi;

// "Artist - Title (Official Video)" -> "Artist_Title"
function cleanTitle(title: string): string {
  return title
    .replace(noiseTag, "")
    .replace(/\s+[-–—|]\s+/, "_")
    .trim();
}

// Captures stdout, or streams it to the terminal when `capture` is false.
function ytDlp(args: string[], capture = true): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn("yt-dlp", ["--no-playlist", ...args], {
      stdio: ["ignore", capture ? "pipe" : "inherit", "inherit"],
    });
    let out = "";
    proc.stdout?.on("data", (d) => (out += d));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0
        ? resolve(out.trim())
        : reject(new Error(`yt-dlp exited with code ${code}`)),
    );
  });
}

async function download(url: string, name: string): Promise<string> {
  await ytDlp(
    [
      "-x",
      "--audio-format",
      "mp3",
      "-o",
      `${publicDir}/${name}.%(ext)s`,
      url,
    ],
    false,
  );
  return path.resolve(publicDir, `${name}.mp3`);
}

// Prompts for anything not passed in; resolves to the saved mp3 path.
export async function downloadSong(url?: string, input?: string) {
  url ??= await ask("URL: ");
  if (!url) throw new Error("No URL given");
  input ??= await ask(
    "File name (e.g. artist_song-title, blank = video title): ",
  );

  const name = toFileName(
    input || cleanTitle(await ytDlp(["--print", "title", url])),
  );
  if (!name) throw new Error("Could not derive a file name");

  const existing = path.resolve(publicDir, `${name}.mp3`);
  if (fs.existsSync(existing)) {
    console.log(`${name}.mp3 already exists, skipping download`);
    return existing;
  }

  console.log(`Downloading ${url} as ${name}.mp3...`);
  const file = await download(url, name);
  console.log(`Saved ${file}`);
  return file;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  downloadSong(process.argv[2], process.argv[3]);
}
