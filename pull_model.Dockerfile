#syntax = docker/dockerfile:1.4
FROM ollama/ollama:latest AS ollama
FROM babashka/babashka:latest
# just using as a client - never as a server
COPY --from=ollama /bin/ollama ./bin/ollama
COPY <<EOF pull_model.clj
(ns pull-model
  (:require [babashka.process :as process]
            [clojure.core.async :as async]))
(try
  (let [llm (get (System/getenv) "LLM")
        url (get (System/getenv) "OLLAMA_BASE_URL")]
    (println (format "pulling ollama model %s using %s" llm url))
    
    ;; Pull the backup model (llama3) first
    (println "Pulling backup model llama3...")
    (let [backup-done (async/chan)]
      (async/go-loop [n 0]
        (let [[v _] (async/alts! [backup-done (async/timeout 5000)])]
          (if (= :stop v) :stopped (do (println (format "... pulling backup model (%ss)" (* n 10))) (recur (inc n))))))
      (try
        (process/shell {:env {"OLLAMA_HOST" url "HOME" (System/getProperty "user.home")} :out :inherit :err :inherit} "bash -c './bin/ollama show llama3 > /dev/null || ./bin/ollama pull llama3'")
        (catch Exception e 
          (println (format "Warning: Could not pull backup model: %s" (.getMessage e)))))
      (async/>!! backup-done :stop))
    
    ;; Now pull the main requested model
    (if (and llm url)
      ;; Allow any model name, but skip OpenAI and other external API-based models
      (if (not (#{"gpt-4" "gpt-3.5" "claudev2" "gpt-4o" "gpt-4-turbo"} llm))
        (let [done (async/chan)]
          (async/go-loop [n 0]
            (let [[v _] (async/alts! [done (async/timeout 5000)])]
              (if (= :stop v) :stopped (do (println (format "... pulling model %s (%ss) - will take several minutes" llm (* n 10))) (recur (inc n))))))
          (println (format "Starting to pull model: %s" llm))
          (process/shell {:env {"OLLAMA_HOST" url "HOME" (System/getProperty "user.home")} :out :inherit :err :inherit} (format "bash -c './bin/ollama show %s > /dev/null || ./bin/ollama pull %s'" llm llm))
          (async/>!! done :stop))
        (println (format "Skipping pull for external API model: %s" llm)))
      (println "OLLAMA model only pulled if both LLM and OLLAMA_BASE_URL are set")))
  (catch Throwable e 
    (println (format "Error pulling models: %s" (.getMessage e)))
    ;; Don't exit with error to allow continuing even if one model fails
    ))
EOF
ENTRYPOINT ["bb", "-f", "pull_model.clj"]

