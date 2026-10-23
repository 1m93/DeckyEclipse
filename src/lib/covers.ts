import { backend } from "../backend";
import type { SteamGame } from "../types";

/** Size of Moonlight's box art (and Steam's portrait capsule). */
const COVER_WIDTH = 600;
const COVER_HEIGHT = 900;
const LOAD_TIMEOUT_MS = 10000;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timer = window.setTimeout(() => reject(new Error("Timed out loading the cover")), LOAD_TIMEOUT_MS);
    image.onload = () => {
      window.clearTimeout(timer);
      resolve(image);
    };
    image.onerror = () => {
      window.clearTimeout(timer);
      reject(new Error("The cover could not be read"));
    };
    image.src = src;
  });
}

/**
 * Re-encode a cover as a 600×900 PNG (base64, no data: prefix). Sunshine only
 * shows PNG box art, while Steam caches covers as JPEG. The image fills the
 * frame and is cropped, like Steam's own grid.
 */
export async function coverToPng(mime: string, base64: string): Promise<string> {
  const image = await loadImage(`data:${mime};base64,${base64}`);
  const canvas = document.createElement("canvas");
  canvas.width = COVER_WIDTH;
  canvas.height = COVER_HEIGHT;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("No canvas to convert the cover");
  const scale = Math.max(COVER_WIDTH / image.width, COVER_HEIGHT / image.height);
  const width = image.width * scale;
  const height = image.height * scale;
  context.drawImage(image, (COVER_WIDTH - width) / 2, (COVER_HEIGHT - height) / 2, width, height);
  return canvas.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

/** Store a game's cover for Sunshine. False when there is none; the game is added without it. */
async function saveCover(game: SteamGame): Promise<boolean> {
  try {
    const cover = await backend.sunshineGameCover(game.appId);
    if (!cover.ok || !cover.mime || !cover.data) return false;
    const png = await coverToPng(cover.mime, cover.data);
    return (await backend.sunshineSaveGameCover(game.appId, png)).ok;
  } catch (err) {
    console.warn(`[Eclipse] no cover for ${game.name}`, err);
    return false;
  }
}

/**
 * Add Steam games to the Sunshine on this device, with their covers.
 * `onProgress` gets how many covers are done.
 */
export async function addGamesToSunshine(
  games: SteamGame[],
  onProgress?: (done: number, total: number) => void,
): Promise<{ ok: boolean; error: string | null; added: number }> {
  let done = 0;
  for (const game of games) {
    await saveCover(game);
    done += 1;
    onProgress?.(done, games.length);
  }
  const res = await backend.sunshineAddGames(games);
  return { ok: res.ok, error: res.error, added: res.added ?? 0 };
}
