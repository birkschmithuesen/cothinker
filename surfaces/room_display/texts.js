// Every English UI string of the room display (issue #173: English UI, English keys).
export const TEXT = {
  brand: "CoThinker",
  views: { live: "Live", topics: "Topics", results: "Results", queue: "Queue", share: "Talk share", memory: "Memory" },
  headings: { "left-agenda": "Agenda", "left-speaker": "Speaking now", "left-queue": "Queue" },
  resultsHeadings: { decisions: "Decisions", tasks: "Tasks", questions: "Open questions" },
  empty: {
    live: "Nothing said yet.", topics: "No topics yet.", results: "No results yet.",
    queue: "Nobody in the queue.", share: "No talk share yet.",
    memory: "No knowledge hits for the current topic.",
    "results-decisions": "No decisions.", "results-tasks": "No tasks.",
    "results-questions": "No open questions.",
    "left-agenda": "No agenda.", "left-speaker": "Speaker detection not active.",
    "left-queue": "Nobody in the queue.",
  },
  speakerNobody: "Nobody is speaking right now.",
  noPerson: "no person", noDue: "no due date",
  topicStatus: { done: "done", current: "current", next: "next", open: "open" },
  state: {
    idle: "Robo", listening: "Robo listens", working: "Robo is working …",
    answer: "Answer ready", offline: "offline",
  },
  fault: "Fault", overrun: "overrun", since: "since", unknownSpeaker: "Unknown",
  memoryQuery: "On topic:",
  mode: { rest: "Live", auto: "Auto ·", pinned: "Pinned ·" },
  menu: "Choose view (M)",
};
