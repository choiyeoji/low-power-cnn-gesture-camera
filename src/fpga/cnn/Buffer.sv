`timescale 1ns / 1ps

module pingpongBuffer #(
    parameter A_ADDR_WIDTH = 64 * 64 * 16,
    parameter B_ADDR_WIDTH = 32 * 32 * 32,
    parameter DATA_WIDTH   = 12
) (
    //buffer ctrl
    input logic w_sel,
    //write
    input logic wclk,
    input logic we,
    input logic [$clog2(A_ADDR_WIDTH)-1:0] wAddr,
    input logic [DATA_WIDTH-1:0] wData,
    //read
    input logic rclk,
    input logic [$clog2(A_ADDR_WIDTH)-1:0] rAddr,
    output logic [DATA_WIDTH-1:0] rData
);

    logic                  r_sel;
    logic                  we_A;
    logic                  we_B;
    logic [DATA_WIDTH-1:0] rData_A;
    logic [DATA_WIDTH-1:0] rData_B;

    assign r_sel = ~w_sel;

    assign we_A  = (~w_sel && we);
    assign we_B  = (w_sel && we);

    assign rData = (r_sel) ? rData_B : rData_A;

    frameBuffer #(
        .ADDR_WIDTH(A_ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) U_frameBuffer_A (
        // write side
        .wclk (wclk),
        .we   (we_A),
        .wAddr(wAddr),
        .wData(wData),
        // read side
        .rclk (rclk),
        .rAddr(rAddr),
        .rData(rData_A)
    );

    frameBuffer #(
        .ADDR_WIDTH(B_ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) U_frameBuffer_B (
        .wclk (wclk),
        .we   (we_B),
        .wAddr(wAddr[$clog2(B_ADDR_WIDTH)-1:0]),
        .wData(wData),
        .rclk (rclk),
        .rAddr(rAddr[$clog2(B_ADDR_WIDTH)-1:0]),
        .rData(rData_B)
    );


endmodule

module frameBuffer #(
    parameter ADDR_WIDTH = 128 * 128,
    parameter DATA_WIDTH = 12
) (
    // write side
    input  logic                          wclk,
    input  logic                          we,
    input  logic [$clog2(ADDR_WIDTH)-1:0] wAddr,
    input  logic [        DATA_WIDTH-1:0] wData,
    // read side
    input  logic                          rclk,
    input  logic [$clog2(ADDR_WIDTH)-1:0] rAddr,
    output logic [        DATA_WIDTH-1:0] rData
);

    (* ram_style = "block" *) logic [DATA_WIDTH-1:0] mem[0:(ADDR_WIDTH)-1];

    //write
    always_ff @(posedge wclk) begin
        if (we) begin
            mem[wAddr] <= wData;
        end
    end
    //read
    always_ff @(posedge rclk) begin
        rData <= mem[rAddr];
    end
endmodule

module weight_mem #(
    parameter WT_DEPTH = 11243,  // ROM의 전체 방 개수 (Absolute)
    parameter MAX_LAYER_WORDS = 8224,   // 단일 레이어 최대 접근 횟수 (fc1 기준)
    parameter DATA_WIDTH = 64
) (
    input  logic                               rclk,
    input  logic [                        2:0] layer,
    input  logic [$clog2(MAX_LAYER_WORDS)-1:0] rAddr,
    output logic [             DATA_WIDTH-1:0] rData
);
    localparam CONV1_START_ADDR = 0;
    localparam CONV2_START_ADDR = 26;
    localparam CONV3_START_ADDR = 618;
    localparam FC1_START_ADDR = 2954;
    localparam FC2_START_ADDR = 11178;

    logic [$clog2(WT_DEPTH)-1:0] decode_raddr;

    always_comb begin
        decode_raddr = 0;
        case (layer)
            1: decode_raddr = CONV1_START_ADDR + rAddr;
            2: decode_raddr = CONV2_START_ADDR + rAddr;
            3: decode_raddr = CONV3_START_ADDR + rAddr;
            4: decode_raddr = FC1_START_ADDR + rAddr;
            5: decode_raddr = FC2_START_ADDR + rAddr;
            default: decode_raddr = 0;
        endcase
    end

    rom #(
        .ADDR_WIDTH($clog2(WT_DEPTH)),
        .DATA_WIDTH(DATA_WIDTH),
        .DEPTH(WT_DEPTH)
    ) U_weight_rom (
        // read side
        .rclk (rclk),
        .rAddr(decode_raddr),
        .rData(rData)
    );

endmodule


module rom #(
    parameter ADDR_WIDTH = 14,
    parameter DATA_WIDTH = 64,
    parameter DEPTH = 11187
) (
    // read side
    input  logic                  rclk,
    input  logic [ADDR_WIDTH-1:0] rAddr,
    output logic [DATA_WIDTH-1:0] rData
);

    (* ram_style = "block" *) logic [DATA_WIDTH-1:0] mem[0:DEPTH-1];

    // Vivado initializes this inferred block RAM from the project-managed
    // memory file. Keep the filename literal for synthesis compatibility.
    initial begin
        $readmemh("int8_weights_conv_fc_be64.mem", mem);
    end

    // read 
    always_ff @(posedge rclk) begin
        rData <= mem[rAddr];
    end

endmodule
