import { MdScreenShare, MdSportsEsports } from "react-icons/md";

/**
 * Plugin icon: an eclipse, the moon (ring) passing in front of the sun
 * (crescent). Drawn in currentColor so it follows Steam's tab colors like the
 * react-icons glyphs.
 */
export function EclipseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor" aria-hidden="true">
      <path d="M8.382 6.166A8 8 0 1 0 17.834 15.618A7.2 7.2 0 0 1 8.382 6.166Z" />
      <circle cx="15" cy="9" r="5.8" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

/** Play: stream games from a PC to this device. */
export function PlayIcon() {
  return <MdSportsEsports />;
}

/** Share: other devices stream this device's screen. */
export function ShareIcon() {
  return <MdScreenShare />;
}
