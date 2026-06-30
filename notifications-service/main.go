// notifications-service — el servicio políglota del sistema.
//
// Mismo contrato que los demás (HTTP + JSON), pero escrito en Go. Para el resto
// del clúster da igual el lenguaje: lo que importa es la interfaz y la imagen.
// Sin base de datos: recibe la notificación y la registra (en producción saldría
// a email / Slack / PagerDuty).
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
)

type notification struct {
	Person  string `json:"person"`
	Email   string `json:"email"`
	Message string `json:"message"`
}

func health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}

func notify(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	var n notification
	if err := json.NewDecoder(r.Body).Decode(&n); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if n.Email == "" || n.Message == "" {
		http.Error(w, `{"error":"email and message are required"}`, http.StatusBadRequest)
		return
	}

	// En un sistema real aquí saldría el email / push. Para el lab, lo logueamos.
	log.Printf("NOTIFY -> %s <%s>: %s", n.Person, n.Email, n.Message)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "sent", "to": n.Email})
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	http.HandleFunc("/health", health)
	http.HandleFunc("/notify", notify)
	log.Printf("notifications-service escuchando en :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
