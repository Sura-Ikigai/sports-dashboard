"use client";

import { useState } from "react";
import { motion } from "framer-motion";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export function FavoriteButton({ teamId, initialFavorited }: { teamId: string; initialFavorited: boolean }) {
  const [favorited, setFavorited] = useState(initialFavorited);
  const [error, setError] = useState(false);

  async function handleClick() {
    const previous = favorited;

    setFavorited(!previous);
    setError(false);

    try {
      const response = await fetch(`${API_URL}/nba/favorite/${teamId}`, { method: "POST" });
      if (!response.ok) throw new Error("Failed to save favorite");
    } catch {
      setFavorited(previous);
      setError(true);
      setTimeout(() => setError(false), 2000);
    }
  }

  return (
    <motion.button
      onClick={handleClick}
      whileTap={{ scale: 0.85 }}
      animate={error ? { x: [-4, 4, -4, 4, 0] } : {}}
      transition={{ duration: 0.3 }}
      className={`text-2xl ${favorited ? "text-yellow-400" : "text-gray-400"}`}
    >
      {favorited ? "★" : "☆"}
    </motion.button>
  );
}
