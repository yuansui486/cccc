import { useRef, useState } from "react";
import type { PointerEventHandler } from "react";
import { useWebPetStore } from "../../stores/useWebPetStore";
import {
  WEB_PET_BUBBLE_SIZE,
  WEB_PET_DRAG_THRESHOLD_PX,
  WEB_PET_VIEWPORT_MARGIN,
} from "./constants";

type PointerState = {
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
  dragging: boolean;
} | null;

function clampPosition(x: number, y: number) {
  const maxX = Math.max(
    WEB_PET_VIEWPORT_MARGIN,
    window.innerWidth - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
  );
  const maxY = Math.max(
    WEB_PET_VIEWPORT_MARGIN,
    window.innerHeight - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
  );

  return {
    x: Math.min(Math.max(x, WEB_PET_VIEWPORT_MARGIN), maxX),
    y: Math.min(Math.max(y, WEB_PET_VIEWPORT_MARGIN), maxY),
  };
}

export function useWebPetDrag() {
  const position = useWebPetStore((state) => state.position);
  const setPosition = useWebPetStore((state) => state.setPosition);
  const togglePanel = useWebPetStore((state) => state.togglePanel);
  const [isDragging, setIsDragging] = useState(false);
  const pointerStateRef = useRef<PointerState>(null);

  const onPointerDown: PointerEventHandler<HTMLDivElement> = (event) => {
    if (!event.isPrimary || event.button !== 0) {
      return;
    }

    pointerStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      dragging: false,
    };

    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };

  const onPointerMove: PointerEventHandler<HTMLDivElement> = (event) => {
    const pointerState = pointerStateRef.current;
    if (!pointerState || pointerState.pointerId !== event.pointerId) {
      return;
    }

    const deltaX = event.clientX - pointerState.startX;
    const deltaY = event.clientY - pointerState.startY;

    if (!pointerState.dragging && Math.hypot(deltaX, deltaY) < WEB_PET_DRAG_THRESHOLD_PX) {
      return;
    }

    if (!pointerState.dragging) {
      pointerState.dragging = true;
      setIsDragging(true);
    }

    setPosition({
      x: pointerState.originX + deltaX,
      y: pointerState.originY + deltaY,
    });
  };

  const onPointerUp: PointerEventHandler<HTMLDivElement> = (event) => {
    const pointerState = pointerStateRef.current;
    if (!pointerState || pointerState.pointerId !== event.pointerId) {
      return;
    }

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    pointerStateRef.current = null;

    if (!pointerState.dragging) {
      setIsDragging(false);
      togglePanel();
      return;
    }

    setPosition(clampPosition(position.x, position.y));
    setIsDragging(false);
  };

  return {
    position,
    isDragging,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
    },
  };
}
