from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel, QMessageBox

class JanelaPrincipal(QMainWindow):
    def __init__(self, controlador):
        super().__init__()
        self.controlador = controlador
        self.setWindowTitle("Sistema de Integração SETTE")
        
        layout = QVBoxLayout()
        self.entrada_serial = QLineEdit()
        self.entrada_serial.setPlaceholderText("Bipe o Serial (10 dígitos)")
        
        btn_estanque = QPushButton("Testar Estanqueidade")
        btn_estanque.clicked.connect(self.processar_estanqueidade)
        
        self.status_label = QLabel("Aguardando operação...")
        
        layout.addWidget(QLabel("Serial:"))
        layout.addWidget(self.entrada_serial)
        layout.addWidget(btn_estanque)
        layout.addWidget(self.status_label)
        
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def processar_estanqueidade(self):
        serial = self.entrada_serial.text()
        sucesso = self.controlador.executar_fluxo_estanqueidade(serial)
        if sucesso:
            self.status_label.setText("Enviado e Salvo!")
        else:
            self.status_label.setText("Falha no envio/banco.")