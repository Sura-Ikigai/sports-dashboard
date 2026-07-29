"use client";

import { useState } from "react";
import { useDebounce } from "@/hooks/useDebounce";
import { useFetch } from "@/hooks/useFetch";

interface Team {
  id: number;
  name: string;
  abbreviation: string | null;
  logo_url: string | null;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export function TeamSearch() {
  const [input, setInput] = useState("");
  const debouncedInput = useDebounce(input, 400);

  const { data: results, loading } = useFetch<Team[]>(
    `${API_URL}/nba/teams${debouncedInput ? `?q=${encodeURIComponent(debouncedInput)}` : ""}`
  );

  return (
    <div className="p-4">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="Search teams..."
        className="border rounded px-3 py-2 text-black"
      />
      {debouncedInput && (
        <ul className="mt-2">
          {loading && <li>Searching...</li>}
          {results?.map((team) => (
            <li key={team.id}>{team.name} ({team.abbreviation})</li>
          ))}
        </ul>
      )}
    </div>
  );
}
