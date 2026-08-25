"""
ArUco board tracker for Edge DM. Runs standalone alongside app.py, using an
overhead USB camera to track player/enemy positions on an 8x8 grid
(5 ft per square, standard D&D combat scale).

SETUP:
1. Print or draw 4 ArUco markers with IDs 0, 1, 2, 3 at the four corners of
   your play grid (top-left, top-right, bottom-right, bottom-left, in that
   order). These calibrate the grid once at startup.
2. Give every player/enemy miniature its own ArUco marker, and register its
   ID in MARKER_TO_ENTITY below, mapping to the matching player_id used
   elsewhere in the system (upload_sheet, current_enemy_id, etc).
3. Mount the camera overhead, pointed straight down at the grid.
4. Confirm your camera's device index with: ls /dev/video*  (usually 0,
   but if a USB sound card or other device grabs an index first, it may
   need adjusting).
"""

import cv2
import numpy as np
import requests
import time

# ---------------------------------------------------------------- SETTINGS
CAMERA_INDEX = 0  # TODO: confirm with `ls /dev/video*` — try 1, 2 if 0 fails
GRID_SIZE = 8               # 8x8 board
FEET_PER_SQUARE = 5         # standard D&D combat square
UPDATE_URL = "http://127.0.0.1:5000/api/update_positions"
POLL_INTERVAL = 1.5         # seconds between position updates sent to backend

CORNER_MARKER_IDS = [0, 1, 2, 3]  # top-left, top-right, bottom-right, bottom-left

# Map each ArUco marker ID to the character/enemy ID it represents —
# these must match the player_id / current_enemy_id used everywhere else
# in the system.
MARKER_TO_ENTITY = {
    10: "char_1787298313377",   # TODO: replace with your real player marker/ID pairs
    20: "goblin_scout_1",       # TODO: replace with your real enemy marker/ID pairs
}

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
ARUCO_PARAMS = cv2.aruco.DetectorParameters()
DETECTOR = cv2.aruco.ArucoDetector(ARUCO_DICT, ARUCO_PARAMS)


# ---------------------------------------------------------------- CALIBRATION
def find_grid_homography(corners_by_id):
    """Given the 4 corner markers' pixel centers, builds a homography that
    maps any pixel coordinate to a (col, row) grid position 0..GRID_SIZE-1."""
    src_pts = np.array([
        corners_by_id[CORNER_MARKER_IDS[0]],
        corners_by_id[CORNER_MARKER_IDS[1]],
        corners_by_id[CORNER_MARKER_IDS[2]],
        corners_by_id[CORNER_MARKER_IDS[3]],
    ], dtype=np.float32)

    dst_pts = np.array([
        [0, 0],
        [GRID_SIZE, 0],
        [GRID_SIZE, GRID_SIZE],
        [0, GRID_SIZE],
    ], dtype=np.float32)

    H, _ = cv2.findHomography(src_pts, dst_pts)
    return H


def pixel_to_grid(H, pixel_xy):
    """Applies the calibration homography to convert a pixel position into
    a grid (col, row) position, clamped to the board."""
    pt = np.array([[pixel_xy]], dtype=np.float32)
    mapped = cv2.perspectiveTransform(pt, H)[0][0]
    col = max(0, min(GRID_SIZE - 1, int(mapped[0])))
    row = max(0, min(GRID_SIZE - 1, int(mapped[1])))
    return col, row


# ---------------------------------------------------------------------- MAIN
def main():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"ERROR: could not open camera at index {CAMERA_INDEX}.")
        print("Check `ls /dev/video*` and update CAMERA_INDEX.")
        return

    print("ArUco tracker running. Looking for corner markers to calibrate...")
    homography = None
    last_sent = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Camera read failed, retrying...")
            time.sleep(0.5)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = DETECTOR.detectMarkers(gray)

        if ids is None:
            continue

        ids = ids.flatten()
        centers_by_id = {}
        for i, marker_id in enumerate(ids):
            c = corners[i][0]
            center = (float(c[:, 0].mean()), float(c[:, 1].mean()))
            centers_by_id[int(marker_id)] = center

        # Calibrate (or re-calibrate) whenever all 4 corners are visible —
        # handles the camera or board being nudged mid-session.
        if all(cid in centers_by_id for cid in CORNER_MARKER_IDS):
            homography = find_grid_homography(centers_by_id)

        if homography is None:
            print("Waiting for all 4 corner markers to be visible...")
            continue

        # Map every tracked entity marker to a grid position.
        positions = {}
        for marker_id, entity_id in MARKER_TO_ENTITY.items():
            if marker_id in centers_by_id:
                col, row = pixel_to_grid(homography, centers_by_id[marker_id])
                positions[entity_id] = {"x": col, "y": row}

        now = time.time()
        if positions and (now - last_sent) >= POLL_INTERVAL:
            try:
                requests.post(UPDATE_URL, json={"positions": positions}, timeout=5)
                print(f"Positions updated: {positions}")
            except Exception as e:
                print(f"Could not reach backend: {e}")
            last_sent = now

    cap.release()


if __name__ == "__main__":
    main()