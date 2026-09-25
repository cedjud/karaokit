import fs from "fs";
import path from "path";
import readline from "readline/promises";
import { fileURLToPath } from "url";
import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

const audioDir = "./public/audio";
const transcriptionDir = "./public/transcripts";

async function pickFile(): Promise<string> {
  const files = fs
    .readdirSync(audioDir)
    .filter((f) => f.toLowerCase().endsWith(".mp3"))
    .sort();

  if (files.length === 0) {
    throw new Error(`No mp3 files found in ${audioDir}`);
  }

  files.forEach((f, i) => {
    const done = fs.existsSync(
      path.join(transcriptionDir, `${path.parse(f).name}.json`),
    );
    console.log(`${i + 1}) ${f}${done ? " (transcribed)" : ""}`);
  });

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  try {
    while (true) {
      const answer = await rl.question(`Pick a file [1-${files.length}]: `);
      const index = Number(answer.trim()) - 1;
      if (Number.isInteger(index) && files[index]) return files[index];
      console.log("Invalid choice.");
    }
  } finally {
    rl.close();
  }
}

// Transcribes an mp3 (path), or prompts to pick one from audioDir.
export async function transcribe(filePath?: string) {
  filePath ??= path.join(audioDir, await pickFile());
  const name = path.parse(filePath).name;

  console.log(`Transcribing ${filePath}...`);
  const transcription = await openai.audio.transcriptions.create({
    file: fs.createReadStream(filePath),
    model: "whisper-1",
    response_format: "verbose_json",
    timestamp_granularities: ["word", "segment"],
    language: "en",
    // Whisper treats the prompt as preceding transcript, not instructions,
    // so it's written as sample lyrics to set the style (vocalizations, repeats).
    prompt: "Oh, oh, oh. La, la, la. Yeah, yeah. Na na na, na na na.",
  });

  const outPath = path.join(transcriptionDir, `${name}.json`);
  fs.writeFileSync(outPath, JSON.stringify(transcription, null, 2));
  console.log(`Saved ${outPath}`);
  return outPath;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  transcribe(process.argv[2]);
}
