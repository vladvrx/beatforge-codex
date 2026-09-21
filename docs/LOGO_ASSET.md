# Transparent BeatForge logo

The Studio uses `web/assets/beat-forge-logo.png`, an RGBA image with transparent background and a small red/blue neon glow. The old radial CSS mask and screen blend were removed. The static demo was rebuilt and the result checked in the browser against the charcoal panel.

Edited with the built-in image-generation tool. Final prompt:

> Extract the original BEAT FORGE neon logo onto actual transparency. Deliver a transparent-background RGBA PNG with an alpha channel, alpha=0 outside the neon lettering and its small outer glow. The previous attempt drew checkerboard pixels into an opaque RGB image; that is NOT transparency and must not be repeated. Use actual background removal/transparency output, not a visual simulation. NO checkerboard pixels, NO white, gray or black backdrop. Keep original thin italic letter shape exactly: red BEAT above blue FORGE. Preserve only lettering plus a tight, subtle semi-transparent colored halo. This is an existing UI asset cleanup, not logo redesign. Canvas transparent everywhere else. Real alpha transparency is essential.
