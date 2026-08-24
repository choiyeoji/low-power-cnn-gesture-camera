`timescale 1ns / 1ps

module fc_quantizer #(
    parameter ACC_WIDTH = 32
) (
    input  logic                        clk,
    input  logic                        rst_n,
    input  logic                        valid_in,
    input  logic signed [ACC_WIDTH-1:0] accumulator_s32,
    output logic                        valid_out,
    output logic        [7:0]           quantized_u8
);

    logic signed [7:0] quantized_s8;

    // signed accumulator의 고정 상위 byte를 signed int8 raw pattern으로 사용한다.
    assign quantized_s8 = $signed(accumulator_s32[31:24]);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            valid_out    <= 1'b0;
            quantized_u8 <= 8'd0;
        end
        else begin
            valid_out <= valid_in;

            if (valid_in) begin
                // 추가 보정 연산 없이 accumulator의 [31:24]만 등록한다.
                quantized_u8 <= quantized_s8;
            end
        end
    end

endmodule
