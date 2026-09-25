import { downloadSong } from "./download.ts";
import { separate } from "./separate.ts";
import { transcribe } from "./transcribe.ts";

const file = await downloadSong(process.argv[2], process.argv[3]);
await separate(file);
await transcribe(file);
