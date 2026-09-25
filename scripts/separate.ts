import { spawn } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import readline from "readline/promises";
import { fileURLToPath } from "url";

const outDir = "./public/stems";
const model = "htdemucs";

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

function demucs(file: string, tmpDir: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn(
      "uvx",
      [
        "--with",
        "numpy",
        "demucs",
        "--two-stems=vocals",
        "--mp3",
        "-n",
        model,
        "-o",
        tmpDir,
        file,
      ],
      { stdio: "inherit" },
    );
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0
        ? resolve()
        : reject(new Error(`demucs exited with code ${code}`)),
    );
  });
}

// Resolves to the folder holding vocals.mp3 and no_vocals.mp3.
export async function separate(filePath?: string) {
  filePath ??= await ask("Audio file: ");
  if (!filePath) throw new Error("No file given");
  if (!fs.existsSync(filePath)) throw new Error(`${filePath} not found`);

  const name = path.basename(filePath, path.extname(filePath));
  const dir = path.resolve(outDir, name);
  if (fs.existsSync(path.join(dir, "vocals.mp3"))) {
    console.log(`${name} already separated, skipping`);
    return dir;
  }

  console.log(`Separating vocals from ${name}...`);
  // demucs nests output under <model>/<track>; run in a temp dir and move the stems out.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "demucs-"));
  try {
    await demucs(filePath, tmpDir);
    fs.mkdirSync(dir, { recursive: true });
    for (const stem of ["vocals.mp3", "no_vocals.mp3"]) {
      fs.copyFileSync(path.join(tmpDir, model, name, stem), path.join(dir, stem));
    }
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
  console.log(`Saved ${dir}`);
  return dir;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  separate(process.argv[2]);
}
