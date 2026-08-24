`timescale 1ns / 1ps

module jetson_controller (
    input wire clk,
    input wire resetn,
    input wire result,
    input wire done,

    input  wire jetson_i,
    output reg  jetson_o,
    output wire person_led,
    output wire jetson_led,
    output reg  cnn_enable,
    // ★ 연속 감지 진행률 (SPI 테일로 Jetson 대기화면 게이지에 표시)
    output wire [6:0] person_cnt
);

    localparam [1:0] IDLE = 2'b00, TRIG = 2'b01, RUN = 2'b10, DONE = 2'b11;

    // ★★ 트리거 조건: result=1 이 TRIG_N 회 '연속'이면 Jetson 세션 시작 ★★
    //   CNN 이 대략 초당 10~15회 도니까 15 ≈ 1초 정도.
    //   데모 속도를 바꾸려면 이 값만 수정 (최대 127).
    localparam [6:0] TRIG_N = 7'd15;

    reg [1:0] current_state, next_state;

    reg  [2:0] jetson_i_sync;
    wire       jetson_i_signal;

    assign jetson_led = jetson_i;
    assign person_led = result;

    reg [6:0] count;
    reg       start;

    assign person_cnt = count;

    always @(posedge clk) begin
        if (!resetn) begin
            count <= 7'd0;
            start <= 1'b0;
        end else begin
            if (current_state == TRIG) start <= 1'b0;
            if (done) begin
                if (result) begin
                    if (count == TRIG_N - 7'd1) begin
                        start <= 1'b1;
                        count <= 7'd0;
                    end else begin
                        count <= count + 7'd1;
                    end
                end else begin
                    count <= 7'd0;
                end
            end
        end
    end

    always @(posedge clk) begin
        if (!resetn) begin
            jetson_i_sync <= 3'b0;
        end else begin
            jetson_i_sync[0] <= jetson_i;
            jetson_i_sync[1] <= jetson_i_sync[0];
            jetson_i_sync[2] <= jetson_i_sync[1];
        end
    end

    assign jetson_i_signal = jetson_i_sync[2] & ~jetson_i_sync[1];

    always @(posedge clk) begin
        if (!resetn) begin
            current_state <= IDLE;
        end else begin
            current_state <= next_state;
        end
    end

    always @(*) begin
        next_state = current_state;
        jetson_o   = 1'b0;
        cnn_enable = 1'b1;
        case (current_state)
            IDLE: begin
                if (start) begin
                    next_state = TRIG;
                end
            end
            TRIG: begin
                jetson_o = 1'b1;
                if (jetson_i_sync[2]) begin
                    next_state = RUN;
                end
            end
            RUN: begin
                cnn_enable = 1'b0;
                if (jetson_i_signal) begin
                    next_state = DONE;
                end
            end
            DONE: begin
                next_state = IDLE;
            end
        endcase
    end

endmodule
